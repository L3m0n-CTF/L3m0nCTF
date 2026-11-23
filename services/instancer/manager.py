import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from services.instancer.flag import is_valid_flag_shape
from services.instancer.lifetime import DEFAULT_MAX_RENEWALS, DEFAULT_MAX_TTL_MINUTES, GLOBAL_MAX_TTL_MINUTES, MAX_RENEWALS_LIMIT, RenewLimitError
from services.instancer.manifest import ChallengeSpec, Manifest
from services.instancer.safety import assert_safe
from services.instancer.urls import instance_url_scheme

_log = logging.getLogger("l3mon.instancer")

MAX_INSTANCES_PER_TEAM = 3
COOLDOWN_SECONDS = 60
# After an instance DIED on its own (a crash, the memory limit): the team may start it again after this short wait, not after the full stop cooldown.
CRASH_COOLDOWN_SECONDS = 10
MIN_RENEW_INTERVAL_SECONDS = 60
REAP_WORKERS = 8  # Docker stops and removals that run at the same moment when instances expire together
ABSOLUTE_LIFETIME_SECONDS = 3 * 3600  # 3 hours


class InstancerError(Exception):
    """Base exception for instancer operations."""
    pass


class SlugNotFoundError(InstancerError):
    """Raised when the requested slug is not in the manifest."""
    pass


class QuotaError(InstancerError):
    """Raised when team active instance quota is reached."""
    pass


class CooldownError(InstancerError):
    """Raised when start is requested during challenge cooldown."""
    def __init__(self, retry_after: int, message: str = "Challenge is in cooldown"):
        super().__init__(message)
        self.retry_after = retry_after


class CapacityError(InstancerError):
    """Raised when the host has reached maximum capacity."""
    def __init__(self, retry_after: int = 30, message: str = "No capacity available on this host"):
        super().__init__(message)
        self.retry_after = retry_after


class InstanceState:
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    EXPIRED = "expired"
    ERROR = "error"


class InstanceRecord:
    def __init__(
        self,
        instance_id: str,
        team_id: int,
        challenge_id: int,
        slug: str,
        protocol: str,
        internal_port: int,
        created_at: int,
        expires_at: int,
        allocated_tcp_port: Optional[int] = None,
        state: str = InstanceState.STARTING,
        renewals: int = 0,
        last_renewed_at: Optional[int] = None,
        target_ip: Optional[str] = None,
        container_id: Optional[str] = None,
        max_body_bytes: int = 1048576,
        response_timeout_seconds: int = 120,
    ):
        self.id = instance_id
        self.team_id = team_id
        self.challenge_id = challenge_id
        self.slug = slug
        self.protocol = protocol
        self.internal_port = internal_port
        self.created_at = created_at
        self.expires_at = expires_at
        self.allocated_tcp_port = allocated_tcp_port
        self.state = state
        self.renewals = renewals
        self.last_renewed_at = last_renewed_at
        self.target_ip = target_ip
        self.container_id = container_id
        self.max_body_bytes = max_body_bytes
        self.response_timeout_seconds = response_timeout_seconds

    def to_dict(self, instance_domain: str, tcp_host: str, scheme: str = "http") -> dict:
        url = f"{scheme}://{self.id}.{instance_domain}/" if self.protocol == "http" else None
        host = tcp_host if self.protocol == "tcp" else None
        return {
            "id": self.id,
            "team_id": self.team_id,
            "challenge_id": self.challenge_id,
            "slug": self.slug,
            "state": self.state,
            "protocol": self.protocol,
            "url": url,
            "host": host,
            "port": self.allocated_tcp_port if self.protocol == "tcp" else None,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "renewals": self.renewals,
        }


class InstanceManager:
    """Manages ephemeral per-team challenge instances, lifecycle, quotas, and label reconciliation."""

    def __init__(
        self,
        manifest: Manifest,
        docker_client,
        instance_domain: str = "localhost:8090",
        tcp_host: str = "localhost",
        tcp_port_start: int = 30000,
        tcp_port_count: int = 200,
        max_slots: int = 200,
        max_mem_mb: int = 16384,
        router_container_name: str = "l3mon-router",
        dev_mode: bool = False,
        time_fn: Optional[Callable[[], float]] = None,
        host_name: str = "default",
        agent_mode: bool = False,
        max_concurrent_starts: int = 8,
        start_wait_seconds: float = 25.0,
        mem_overcommit: float = 1.0,
        url_scheme: Optional[str] = None,
        max_ttl_minutes: int = DEFAULT_MAX_TTL_MINUTES,
        max_renewals: int = DEFAULT_MAX_RENEWALS,
    ):
        if not (1024 <= tcp_port_start <= 65535):
            raise ValueError(f"INSTANCER_TCP_PORT_START must be between 1024 and 65535, got {tcp_port_start}")
        if not (1 <= tcp_port_count <= 2000):
            raise ValueError(f"INSTANCER_TCP_PORT_COUNT must be between 1 and 2000, got {tcp_port_count}")
        if tcp_port_start + tcp_port_count - 1 > 65535:
            raise ValueError(
                f"TCP port range [{tcp_port_start}, {tcp_port_start + tcp_port_count - 1}] exceeds 65535"
            )

        if not (1 <= max_ttl_minutes <= GLOBAL_MAX_TTL_MINUTES):
            raise ValueError(f"max_ttl_minutes must be between 1 and {GLOBAL_MAX_TTL_MINUTES}, got {max_ttl_minutes}")
        if not (0 <= max_renewals <= MAX_RENEWALS_LIMIT):
            raise ValueError(f"max_renewals must be between 0 and {MAX_RENEWALS_LIMIT}, got {max_renewals}")
        # The longest any instance lives, whatever its challenge asks for, and how often a studio may renew one (services/instancer/lifetime.py)
        self.max_ttl_minutes = int(max_ttl_minutes)
        self.max_renewals = int(max_renewals)
        for spec in manifest.challenges.values() if hasattr(manifest, "challenges") else []:
            if spec.ttl_minutes > self.max_ttl_minutes:
                _log.warning("Challenge '%s' asks for %d minutes; this host lets an instance live at most %d: it gets %d", spec.slug, spec.ttl_minutes, self.max_ttl_minutes, self.max_ttl_minutes)

        self.manifest = manifest
        self.docker = docker_client
        self.instance_domain = instance_domain
        self.url_scheme = instance_url_scheme(instance_domain, url_scheme)
        self.tcp_host = tcp_host
        self.tcp_port_start = tcp_port_start
        self.tcp_port_count = tcp_port_count
        self.max_slots = max_slots
        self.max_mem_mb = max_mem_mb
        self.router_container_name = router_container_name
        self.dev_mode = dev_mode
        self._time = time_fn or time.time
        self.host_name = host_name
        self.agent_mode = agent_mode
        self._lock = threading.Lock()
        # Only this many starts work on Docker at the same moment (the rest wait their turn, at most start_wait_seconds, then are told "busy, retry in a few
        # seconds"). Found on the real host (21 Nov 2026): 80 starts at once took 28 s in all because Docker does network work one piece at a time; the
        # median start waited 16 s and 55 of 80 were slower than the platform's 10 s timeout.
        self.start_wait_seconds = start_wait_seconds
        # A declared memory limit counts as limit / mem_overcommit against max_mem_mb. 1.0 counts it in full (the safe way); a higher factor is for a host whose
        # challenges have been MEASURED to use far less than they declare (docs/deploy/instancer.md, "Sizing a challenge host")
        self.mem_overcommit = max(1.0, float(mem_overcommit))
        self._start_slots = threading.BoundedSemaphore(max(1, int(max_concurrent_starts)))

        # State storage
        self._instances: dict[str, InstanceRecord] = {}  # instance_id -> record
        self._team_index: dict[int, set[str]] = {}       # team_id -> set of instance_ids
        self._challenge_index: dict[tuple[int, int], str] = {}  # (team_id, challenge_id) -> instance_id
        self._cooldowns: dict[tuple[int, int], int] = {}  # (team_id, challenge_id) -> expiry_ts
        self._allocated_tcp_ports: set[int] = set()

    def _now(self) -> int:
        return int(self._time())

    def reconcile_from_docker(self) -> None:
        """Scan Docker containers with label 'l3mon.instance' and rebuild in-memory state."""
        with self._lock:
            containers = self.docker.list_containers(label_filter={"l3mon.instance": None})
            now = self._now()
            for c in containers:
                labels = c.get("labels", {})
                inst_id = labels.get("l3mon.instance")
                if not inst_id:
                    continue
                try:
                    team_id = int(labels.get("l3mon.team_id", 0))
                    challenge_id = int(labels.get("l3mon.challenge_id", 0))
                    slug = labels.get("l3mon.slug", "")
                    created_at = int(labels.get("l3mon.created_at", now))
                    expires_at = int(labels.get("l3mon.expires_at", now))
                    spec_for_cap = self.manifest.get(labels.get("l3mon.slug", ""))
                    ceiling = self.max_ttl_minutes * 60 if spec_for_cap is None else self._lifetime_seconds(spec_for_cap)
                    expires_at = min(expires_at, created_at + ceiling)  # started before the ceiling existed (45 minutes): cut to it
                    protocol = labels.get("l3mon.protocol", "http")
                    port = int(labels.get("l3mon.port", 8080))
                    renewals = int(labels.get("l3mon.renewals", 0))
                    tcp_port = int(labels.get("l3mon.tcp_port")) if labels.get("l3mon.tcp_port") else None
                except (ValueError, TypeError):
                    continue

                state = InstanceState.RUNNING if c.get("state") in ("running", "created") else InstanceState.ERROR
                spec = self.manifest.get(slug)
                max_body = spec.max_body_bytes if spec else 1048576
                resp_timeout = spec.response_timeout_seconds if spec else 120
                record = InstanceRecord(
                    instance_id=inst_id,
                    team_id=team_id,
                    challenge_id=challenge_id,
                    slug=slug,
                    protocol=protocol,
                    internal_port=port,
                    created_at=created_at,
                    expires_at=expires_at,
                    allocated_tcp_port=tcp_port,
                    state=state,
                    renewals=renewals,
                    target_ip=c.get("ip"),
                    container_id=c.get("id"),
                    max_body_bytes=max_body,
                    response_timeout_seconds=resp_timeout,
                )
                self._instances[inst_id] = record
                self._team_index.setdefault(team_id, set()).add(inst_id)
                self._challenge_index[(team_id, challenge_id)] = inst_id
                if tcp_port is not None:
                    self._allocated_tcp_ports.add(tcp_port)

            # Clean orphan networks (created before a crash without a running container)
            try:
                networks = self.docker.list_networks(label_filter={"l3mon.instance": None})
                for net in networks:
                    labels = net.get("Labels", {}) or {}
                    net_inst_id = labels.get("l3mon.instance")
                    if net_inst_id and net_inst_id not in self._instances:
                        net_id = net.get("Id") or net.get("Name")
                        _log.info("Pruning orphan network %s (instance %s)", net_id, net_inst_id)
                        try:
                            self.docker.disconnect_network(net_id, self.router_container_name)
                        except Exception:
                            pass
                        try:
                            self.docker.remove_network(net_id)
                        except Exception:
                            pass
            except Exception as e:
                _log.warning("Error reconciling orphan networks: %s", e)

    def start_instance(
        self, team_id: int, challenge_id: int, slug: str, flag: str, ttl_seconds: Optional[int] = None
    ) -> tuple[dict, bool]:
        """Start a per-team challenge instance or return existing active one. Returns (instance_dict, created_boolean)."""
        if not is_valid_flag_shape(flag):
            raise ValueError("Flag does not match the required format")

        spec = self.manifest.get(slug)
        if spec is None:
            raise SlugNotFoundError(f"Unknown challenge slug '{slug}'")

        # The studio's instances whose time is up leave first (a quick stop, then Docker): otherwise the host would hand back the dead record for the few seconds before
        # the reaper's next pass, and count it against the studio's quota of three.
        self.reap_expired(team_id)

        with self._lock:
            now = self._now()
            key = (team_id, challenge_id)

            # 1. Idempotency: return existing active instance if one exists
            existing_id = self._challenge_index.get(key)
            if existing_id and existing_id in self._instances:
                inst = self._instances[existing_id]
                if inst.state in (InstanceState.RUNNING, InstanceState.STARTING):
                    return inst.to_dict(self.instance_domain, self.tcp_host, self.url_scheme), False

            # 2. Cooldown check
            cooldown_until = self._cooldowns.get(key, 0)
            if now < cooldown_until:
                retry_after = cooldown_until - now
                raise CooldownError(retry_after=retry_after, message=f"Cooldown active. Retry in {retry_after}s")

            # 3. Quota check: at most 3 live instances per team
            active_team_instances = [
                self._instances[iid]
                for iid in self._team_index.get(team_id, set())
                if iid in self._instances and self._instances[iid].state in (InstanceState.RUNNING, InstanceState.STARTING)
            ]
            if len(active_team_instances) >= MAX_INSTANCES_PER_TEAM:
                raise QuotaError(f"Team active instance quota ({MAX_INSTANCES_PER_TEAM}) reached")

            # 4. Capacity check
            total_active = len([inst for inst in self._instances.values() if inst.state in (InstanceState.RUNNING, InstanceState.STARTING)])
            if total_active >= self.max_slots:
                raise CapacityError(retry_after=30, message="Host slot capacity reached")
            # The memory the running instances may use (their declared limits) plus this one must fit the ceiling of this machine
            # (INSTANCER_MAX_MEM_MB). Found on the real host: only the slot count was checked, so 200 instances of 64 MB or more could be admitted
            # on a machine that has 8 GB.
            if (self._active_mem_mb() + spec.mem_mb) / self.mem_overcommit > self.max_mem_mb:
                raise CapacityError(retry_after=30, message="Host memory capacity reached")

            # 5. Allocate resources
            inst_id = secrets.token_hex(16).lower()  # 32 lowercase hex characters
            allocated_tcp_port = None
            if spec.protocol == "tcp":
                for p in range(self.tcp_port_start, self.tcp_port_start + self.tcp_port_count):
                    if p not in self._allocated_tcp_ports:
                        allocated_tcp_port = p
                        self._allocated_tcp_ports.add(p)
                        break
                if allocated_tcp_port is None:
                    raise CapacityError(retry_after=30, message="No available TCP relay ports")

            # Calculate TTL
            if self.dev_mode and ttl_seconds is not None:
                duration = max(1, min(int(ttl_seconds), self._lifetime_seconds(spec)))  # a shorter test lifetime is allowed; a longer one is not
            else:
                duration = self._lifetime_seconds(spec)
            expires_at = now + duration

            record = InstanceRecord(
                instance_id=inst_id,
                team_id=team_id,
                challenge_id=challenge_id,
                slug=slug,
                protocol=spec.protocol,
                internal_port=spec.port,
                created_at=now,
                expires_at=expires_at,
                allocated_tcp_port=allocated_tcp_port,
                state=InstanceState.STARTING,
                renewals=0,
                max_body_bytes=spec.max_body_bytes,
                response_timeout_seconds=spec.response_timeout_seconds,
            )
            self._instances[inst_id] = record
            self._team_index.setdefault(team_id, set()).add(inst_id)
            self._challenge_index[key] = inst_id

        # Wait for a turn on Docker (see max_concurrent_starts). A start that waits too long gives its reservation back and is told to retry.
        if not self._start_slots.acquire(timeout=self.start_wait_seconds):
            with self._lock:
                self._instances.pop(inst_id, None)
                if team_id in self._team_index:
                    self._team_index[team_id].discard(inst_id)
                if self._challenge_index.get(key) == inst_id:
                    self._challenge_index.pop(key, None)
                if allocated_tcp_port is not None:
                    self._allocated_tcp_ports.discard(allocated_tcp_port)
            raise CapacityError(retry_after=5, message="Host busy starting other instances; retry in a few seconds")
        try:
            # Network creation and Docker calls OUTSIDE self._lock
            net_name = f"l3mon_inst_{inst_id}"
            try:
                self.docker.create_network(net_name, internal=True, labels={"l3mon.instance": inst_id})
            except Exception as e:
                with self._lock:
                    self._instances.pop(inst_id, None)
                    if team_id in self._team_index:
                        self._team_index[team_id].discard(inst_id)
                    if self._challenge_index.get(key) == inst_id:
                        self._challenge_index.pop(key, None)
                    if allocated_tcp_port is not None:
                        self._allocated_tcp_ports.discard(allocated_tcp_port)
                raise InstancerError(f"Failed to create network: {e}")

            # Attach router to network
            try:
                self.docker.connect_network(net_name, self.router_container_name)
            except Exception as e:
                _log.warning("Could not connect router container '%s' to network '%s': %s", self.router_container_name, net_name, e)

            # Build Docker container spec
            container_labels = {
                "l3mon.instance": inst_id,
                "l3mon.team_id": str(team_id),
                "l3mon.challenge_id": str(challenge_id),
                "l3mon.slug": slug,
                "l3mon.created_at": str(now),
                "l3mon.expires_at": str(expires_at),
                "l3mon.protocol": spec.protocol,
                "l3mon.port": str(spec.port),
                "l3mon.renewals": "0",
            }
            if allocated_tcp_port is not None:
                container_labels["l3mon.tcp_port"] = str(allocated_tcp_port)

            container_spec = {
                "Image": spec.image,
                "User": "10001:10001",
                "Env": [f"FLAG={flag}"],
                "Labels": container_labels,
                "HostConfig": {
                    "NetworkMode": net_name,
                    "Privileged": False,
                    "CapDrop": ["ALL"],
                    "CapAdd": [],
                    "SecurityOpt": ["no-new-privileges:true"],
                    "ReadonlyRootfs": spec.read_only,
                    "Init": True,
                    "RestartPolicy": {"Name": "no"},
                    "Binds": [],
                    "VolumesFrom": [],
                    "Devices": [],
                    "NanoCpus": int(spec.cpus * 1_000_000_000),
                    "Memory": spec.mem_mb * 1024 * 1024,
                    "PidsLimit": spec.pids,
                    "PortBindings": {},
                    "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
                    "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m", "max-file": "3"}},
                    "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 2048}],
                },
            }

            # Security verification
            try:
                assert_safe(container_spec, dev_mode=self.dev_mode)
            except Exception:
                try:
                    self.docker.disconnect_network(net_name, self.router_container_name)
                except Exception:
                    pass
                try:
                    self.docker.remove_network(net_name)
                except Exception:
                    pass
                with self._lock:
                    self._instances.pop(inst_id, None)
                    if team_id in self._team_index:
                        self._team_index[team_id].discard(inst_id)
                    if self._challenge_index.get(key) == inst_id:
                        self._challenge_index.pop(key, None)
                    if allocated_tcp_port is not None:
                        self._allocated_tcp_ports.discard(allocated_tcp_port)
                raise

            # Create and start container
            name = inst_id
            try:
                real_cid = self.docker.create_container(container_spec, name=name)
                self.docker.start_container(real_cid)
            except Exception as e:
                # Cleanup on failure
                try:
                    self.docker.remove_container(name)
                except Exception:
                    pass
                try:
                    self.docker.disconnect_network(net_name, self.router_container_name)
                    self.docker.remove_network(net_name)
                except Exception:
                    pass
                with self._lock:
                    self._instances.pop(inst_id, None)
                    if team_id in self._team_index:
                        self._team_index[team_id].discard(inst_id)
                    if self._challenge_index.get(key) == inst_id:
                        self._challenge_index.pop(key, None)
                    if allocated_tcp_port is not None:
                        self._allocated_tcp_ports.discard(allocated_tcp_port)
                raise InstancerError(f"Failed to create/start container: {e}")

            # Retrieve target IP
            target_ip = None
            try:
                cinfo = self.docker.list_containers(label_filter={"l3mon.instance": inst_id})
                if cinfo:
                    target_ip = cinfo[0].get("ip")
            except Exception:
                pass

            with self._lock:
                record.state = InstanceState.RUNNING
                record.target_ip = target_ip
                record.container_id = real_cid
                return record.to_dict(self.instance_domain, self.tcp_host, self.url_scheme), True
        finally:
            self._start_slots.release()

    def get_instance(self, instance_id: str) -> Optional[dict]:
        with self._lock:
            record = self._instances.get(instance_id)
            if not record:
                return None
            return record.to_dict(self.instance_domain, self.tcp_host, self.url_scheme)

    def list_instances(self, team_id: Optional[int] = None) -> list[dict]:
        with self._lock:
            res = []
            for inst in self._instances.values():
                if team_id is None or inst.team_id == team_id:
                    res.append(inst.to_dict(self.instance_domain, self.tcp_host, self.url_scheme))
            return res

    def renew_instance(self, instance_id: str) -> dict:
        """Renew an instance TTL. Refused if renewed < 60s ago or past 3h absolute lifetime cap."""
        with self._lock:
            record = self._instances.get(instance_id)
            if not record:
                raise SlugNotFoundError(f"Instance '{instance_id}' not found")

            now = self._now()
            # 0. Refuse when the studio has used the renewals this host allows (none, by default: an instance lives 15 minutes and then a new one is started)
            if record.renewals >= self.max_renewals:
                if self.max_renewals == 0:
                    raise RenewLimitError(f"An instance runs for {self.max_ttl_minutes} minutes and cannot be extended. Start a new one when it ends.")
                raise RenewLimitError(f"This instance has already been renewed {record.renewals} time{'' if record.renewals == 1 else 's'}, the most this host allows. Start a new one when it ends.")

            # 1. Refuse if renewed less than 60s ago
            if record.last_renewed_at is not None and (now - record.last_renewed_at < MIN_RENEW_INTERVAL_SECONDS):
                raise ValueError("Instance cannot be renewed less than 60 seconds after previous renewal")

            # 2. Refuse if past absolute lifetime cap (3 hours from creation)
            if now - record.created_at >= ABSOLUTE_LIFETIME_SECONDS:
                raise ValueError("Instance has reached its absolute 3-hour lifetime cap")

            spec = self.manifest.get(record.slug)
            new_expiry = min(now + (self._lifetime_seconds(spec) if spec else self.max_ttl_minutes * 60), record.created_at + ABSOLUTE_LIFETIME_SECONDS)

            record.expires_at = new_expiry
            record.last_renewed_at = now
            record.renewals += 1

            return record.to_dict(self.instance_domain, self.tcp_host, self.url_scheme)

    def _lifetime_seconds(self, spec) -> int:
        """How long an instance of this challenge lives: what its manifest asks for, never more than this host's ceiling."""
        return min(spec.ttl_minutes, self.max_ttl_minutes) * 60

    def _active_mem_mb(self) -> float:
        """The declared memory limits of the live instances, in MB. The caller holds the lock. (Counted against the ceiling as limit / mem_overcommit.)"""
        total = 0
        for inst in self._instances.values():
            if inst.state in (InstanceState.RUNNING, InstanceState.STARTING):
                spec = self.manifest.get(inst.slug)
                if spec:
                    total += spec.mem_mb
        return total

    def _begin_stop(self, instance_id: str, cooldown_seconds: int):
        """The quick half of a stop, under the lock and with no Docker call: the instance stops counting against the slots, the memory and the studio's quota, leaves the
        indexes (so no start is handed this record) and leaves the routes. -> what the slow half needs, or None when there is nothing to stop (or it is stopping already)."""
        with self._lock:
            record = self._instances.get(instance_id)
            if not record:
                return None
            if record.state == InstanceState.STOPPING:
                return None

            now = self._now()
            record.state = InstanceState.STOPPING

            # Set cooldown for (team_id, challenge_id)
            key = (record.team_id, record.challenge_id)
            self._cooldowns[key] = now + cooldown_seconds

            if record.allocated_tcp_port is not None:
                self._allocated_tcp_ports.discard(record.allocated_tcp_port)

            if record.team_id in self._team_index:
                self._team_index[record.team_id].discard(instance_id)
            if self._challenge_index.get(key) == instance_id:
                self._challenge_index.pop(key, None)

            return getattr(record, "container_id", None) or instance_id

    def _finish_stop(self, instance_id: str, target_container: str) -> None:
        """The slow half of a stop, outside the lock: Docker stops and removes the container and its network, then the record goes."""
        net_name = f"l3mon_inst_{instance_id}"
        try:
            self.docker.stop_container(target_container)
            self.docker.remove_container(target_container)
        except Exception as e:
            _log.warning("Error stopping container %s: %s", target_container, e)

        try:
            self.docker.disconnect_network(net_name, self.router_container_name)
            self.docker.remove_network(net_name)
        except Exception as e:
            _log.warning("Error removing network %s: %s", net_name, e)

        with self._lock:
            self._instances.pop(instance_id, None)

    def stop_instance(self, instance_id: str, cooldown_seconds: int = COOLDOWN_SECONDS) -> bool:
        """Stop and tear down an instance. Idempotent."""
        target = self._begin_stop(instance_id, cooldown_seconds)
        if target is None:
            return False
        self._finish_stop(instance_id, target)
        return True

    def kill_instances(self, team_id: Optional[int] = None, challenge_id: Optional[int] = None) -> int:
        """Admin kill by team and/or challenge, or all if none given."""
        to_kill = []
        with self._lock:
            for inst in list(self._instances.values()):
                match_team = team_id is None or inst.team_id == team_id
                match_chal = challenge_id is None or inst.challenge_id == challenge_id
                if match_team and match_chal:
                    to_kill.append(inst.id)

        count = 0
        for iid in to_kill:
            if self.stop_instance(iid):
                count += 1
        return count

    def _expired_ids(self, team_id: Optional[int] = None) -> list[str]:
        with self._lock:
            now = self._now()
            return [
                inst.id for inst in list(self._instances.values())
                if inst.state in (InstanceState.RUNNING, InstanceState.STARTING) and now >= inst.expires_at and (team_id is None or inst.team_id == team_id)
            ]

    def reap_expired(self, team_id: Optional[int] = None) -> list[str]:
        """Remove the instances whose time is up (those of one studio when `team_id` is given).

        All of them stop counting at once (the quick half, with no Docker call), then Docker removes them several at a time. With 15-minute lives the instances of the
        opening burst expire together and the studios start again at once, so a wave of hundreds comes every 15 minutes: one thread stopping them one by one (up to five
        seconds each) would leave them running, routable and counted against the slots for minutes."""
        # An instance that ran out its time is not a studio hammering Start and Stop: the studio must be able to start a new one at once, so no restart cooldown is
        # left (a deliberate Stop still leaves the 60 seconds).
        started = []
        for iid in self._expired_ids(team_id):
            target = self._begin_stop(iid, 0)
            if target is not None:
                started.append((iid, target))
        if not started:
            return []
        if len(started) == 1:
            self._finish_stop(*started[0])
        else:
            with ThreadPoolExecutor(max_workers=min(REAP_WORKERS, len(started)), thread_name_prefix="reap") as pool:
                list(pool.map(lambda item: self._finish_stop(*item), started))
        return [iid for iid, _ in started]

    def reap_dead(self) -> list[str]:
        """Tear down the instances whose container is no longer running (it crashed, or the memory limit killed it, or someone removed it).

        Found on the real host: such an instance stayed "running" until its TTL, so the player saw a live instance that answered nothing and could only stop it
        and wait for the full cooldown. Here the instance disappears from the player's list (the page offers Start again) and the team waits only
        CRASH_COOLDOWN_SECONDS. If Docker cannot be asked nothing is changed: a failed question is not a dead container.
        """
        # RUNNING ones whose container died, and ERROR ones: after a reboot or a restart of the agent a record is rebuilt from Docker's labels as ERROR when its
        # container is not running, and it would otherwise stay (with its stopped container and its network) for ever
        with self._lock:
            candidates = [inst.id for inst in self._instances.values() if inst.state in (InstanceState.RUNNING, InstanceState.ERROR)]
        if not candidates:
            return []
        try:
            listed = self.docker.list_containers(label_filter={"l3mon.instance": None})
        except Exception as e:
            _log.warning("Liveness sweep skipped, Docker did not answer: %s", e)
            return []
        alive = {c.get("labels", {}).get("l3mon.instance") for c in listed if c.get("state") in ("running", "created", "restarting")}
        reaped = []
        for iid in candidates:
            if iid not in alive and self.stop_instance(iid, cooldown_seconds=CRASH_COOLDOWN_SECONDS):
                _log.warning("Instance %s was not running any more (crashed or killed); torn down", iid)
                reaped.append(iid)
        return reaped

    def get_capacity(self) -> dict:
        with self._lock:
            active = [inst for inst in self._instances.values() if inst.state in (InstanceState.RUNNING, InstanceState.STARTING)]
            slots_used = len(active)
            mem_mb_used = int(round(self._active_mem_mb() / self.mem_overcommit))  # as counted against the ceiling

            return {
                "host_name": self.host_name,
                "slots_total": self.max_slots,
                "slots_used": slots_used,
                "slots_free": max(0, self.max_slots - slots_used),
                "mem_mb_total": self.max_mem_mb,
                "mem_mb_used": mem_mb_used,
                "mem_mb_free": max(0, self.max_mem_mb - mem_mb_used),
            }

    def get_routes(self) -> list[dict]:
        """Returns the route list for the instance router."""
        with self._lock:
            routes = []
            for inst in self._instances.values():
                if inst.state == InstanceState.RUNNING and inst.target_ip:
                    routes.append({
                        "id": inst.id,
                        "protocol": inst.protocol,
                        "target_ip": inst.target_ip,
                        "target_port": inst.internal_port,
                        "port": inst.allocated_tcp_port,
                        "created_at": inst.created_at,
                        "max_body_bytes": inst.max_body_bytes,
                        "response_timeout_seconds": inst.response_timeout_seconds,
                    })
            return routes

