import json
import logging
import os
import socket
import ssl
import urllib.error
import urllib.request
from typing import Optional

_log = logging.getLogger("l3mon.instances.client")

_override_client = None


def set_client_override(mock):
    global _override_client
    _override_client = mock


def get_instancer_token() -> str:
    path = "/run/secrets/INSTANCER_TOKEN"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception as e:
            _log.warning("Could not read secret file %s: %s", path, e)
    return os.getenv("INSTANCER_TOKEN", "").strip()


# A start takes as long as Docker takes, and in a burst (a hundred teams clicking Start in the same second) that is tens of seconds: on the real challenge host 80
# simultaneous starts took 28 s in all, the median 16 s (21 Nov 2026). The agent answers "busy" itself after 35 s, so 45 s leaves it room to do so.
DEFAULT_START_TIMEOUT_SECONDS = 45


def get_start_timeout() -> float:
    """L3MON_INSTANCER_START_TIMEOUT (seconds) or 45. A value that is not a positive number falls back to the default: it must not break starts."""
    try:
        value = float(os.getenv("L3MON_INSTANCER_START_TIMEOUT", "").strip())
        if value > 0:
            return value
    except ValueError:
        pass
    return DEFAULT_START_TIMEOUT_SECONDS


def get_instancer_url() -> str:
    return os.getenv("INSTANCER_URL", "http://instancer:8300").rstrip("/")


class InstancerClientError(Exception):
    def __init__(self, status: int, error_code: str, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message
        self.retry_after = retry_after


class InstancerClient:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None, timeout: int = 10, ca_cert: Optional[str] = None, start_timeout: Optional[float] = None):
        self.base_url = (base_url or get_instancer_url()).rstrip("/")
        self.token = token or get_instancer_token()
        self.timeout = timeout
        self.start_timeout = start_timeout if start_timeout is not None else get_start_timeout()
        self.ca_cert = ca_cert

    def _request(self, method: str, path: str, body: Optional[dict] = None, timeout: Optional[float] = None) -> tuple[int, dict]:
        if _override_client is not None:
            return _override_client.request(method, path, body)

        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        ssl_context = None
        if self.ca_cert and url.startswith("https://"):
            ssl_context = ssl.create_default_context(cafile=self.ca_cert)

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            kwargs = {"timeout": timeout if timeout is not None else self.timeout}
            if ssl_context is not None:
                kwargs["context"] = ssl_context
            with urllib.request.urlopen(req, **kwargs) as resp:
                status = resp.status
                content = resp.read()
                parsed = json.loads(content.decode("utf-8")) if content else {}
                return status, parsed
        except urllib.error.HTTPError as e:
            content = e.read()
            parsed = {}
            if content:
                try:
                    parsed = json.loads(content.decode("utf-8"))
                except Exception:
                    pass
            retry_after = None
            if "Retry-After" in e.headers:
                try:
                    retry_after = int(e.headers["Retry-After"])
                except ValueError:
                    pass
            error_code = parsed.get("error", "upstream_error")
            message = parsed.get("message", str(e))
            raise InstancerClientError(e.code, error_code, message, retry_after=retry_after)
        except (socket.timeout, TimeoutError) as e:
            # a slow host is not a dead host: its own kind of error, so that the caller does not mark the host down for a slow answer
            _log.warning("Instancer request timed out: %s %s", method, path)
            raise InstancerClientError(504, "timeout", f"The challenge instancer did not answer in time: {e}")
        except Exception as e:
            if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                _log.warning("Instancer request timed out: %s %s", method, path)
                raise InstancerClientError(504, "timeout", f"The challenge instancer did not answer in time: {e}")
            _log.error("Instancer request failed: %s", e)
            raise InstancerClientError(503, "upstream_unavailable", f"Could not reach challenge instancer: {e}")

    def start_instance(self, team_id: int, challenge_id: int, slug: str, flag: str, ttl_seconds: Optional[int] = None) -> dict:
        body = {
            "team_id": team_id,
            "challenge_id": challenge_id,
            "slug": slug,
            "flag": flag,
        }
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        status, data = self._request("POST", "/v1/instances", body=body, timeout=self.start_timeout)
        return data.get("instance", {})

    def get_instance(self, instance_id: str) -> Optional[dict]:
        status, data = self._request("GET", f"/v1/instances/{instance_id}")
        return data.get("instance")

    def list_instances(self, team_id: Optional[int] = None) -> list[dict]:
        path = f"/v1/instances?team_id={team_id}" if team_id is not None else "/v1/instances"
        status, data = self._request("GET", path)
        return data.get("instances", [])

    def renew_instance(self, instance_id: str) -> dict:
        status, data = self._request("POST", f"/v1/instances/{instance_id}/renew")
        return data.get("instance", {})

    def stop_instance(self, instance_id: str) -> None:
        self._request("DELETE", f"/v1/instances/{instance_id}")

    def kill_instances(self, team_id: Optional[int] = None, challenge_id: Optional[int] = None) -> int:
        body = {}
        if team_id is not None:
            body["team_id"] = team_id
        if challenge_id is not None:
            body["challenge_id"] = challenge_id
        status, data = self._request("POST", "/v1/admin/kill", body=body)
        return data.get("stopped", 0)

    def get_capacity(self) -> dict:
        status, data = self._request("GET", "/v1/capacity")
        return data
