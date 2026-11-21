# Runbook 18: challenge instances, the crew-only gate, and the challenge hosts

Written 21 Nov 2026 after the first real-host test round. Background and numbers: `docs/deploy/instancer.md` (sections 4a to 4d).

## Which state is it in?

| Question | How to look |
|---|---|
| Are instance addresses crew-only or open? | `grep INSTANCE_ACCESS_MODE /etc/l3mon/production.env` on the platform VM (`crew` = crew only, `open` = everybody). The central router's `/metrics` shows `instancer_router_access_gate_open` (0 = crew only) and `instancer_router_access_gate_configured` |
| Is the gate refusing people it should let in? | `instancer_router_access_refusals_total{reason}`: `wrong audience` means the crew application's tag is wrong, `expired` / `keys unavailable` means a Cloudflare problem, `gate not configured` means the team name or tag is missing |
| Is a challenge host healthy? | `curl -H "Authorization: Bearer $(sudo cat /etc/l3mon/secrets/INSTANCER_TOKEN)" http://<host private ip>:8300/v1/capacity` on the platform VM: slots and memory used/free |
| How many instances run? | the same call, `slots_used`; the crew API `GET /api/v1/l3mon/admin/instances` (there is no crew page for instances yet: only this API and the kill call) |

## Switch the gate: crew only <-> open

1. Edit `INSTANCE_ACCESS_MODE` in `/etc/l3mon/production.env` (`crew` or `open`). For `crew` also `INSTANCE_ACCESS_TEAM_DOMAIN` (`<name>.cloudflareaccess.com`) and `INSTANCE_ACCESS_AUD` (the crew application's tag, 64 hex characters).
2. `tools/compose.sh up -d router` (only the router restarts; nothing else is touched, players are not affected).
3. `python -m l3mon deploy check` must show `instance addresses` as `ok` (crew) or `todo ... open to everybody` (open, the event setting).
4. Prove it from outside: open an instance address in a private browser window that is NOT signed in to Cloudflare Access. Crew mode must show the Cloudflare sign-in page, never the challenge.

**Before the event:** set `open`, enable the cloud firewall rule `l3mon-chal-allow-tcp-relays` with the relay range (`TCP_PORT_FIRST` to `TCP_PORT_LAST`), and make sure the wildcard DNS record `*` is proxied. **After a test round:** set `crew` and disable that firewall rule again.

## A challenge host does not answer

The platform marks a host down only when it is unreachable or fails, never for a slow or busy answer. Players' existing instances on a down host show `error`.

1. `gcloud compute instances describe l3mon-chal ...` (is it `RUNNING`?). If it stopped or rebooted: it comes back by itself (the stack restarts, the isolation firewall is re-applied). Wait about two minutes.
2. On the host: `sudo docker ps` (agent, router, socket-proxy, registry up?), `sudo iptables -S L3MON-FIREWALL` (9 rules), `curl` the capacity call above.
3. After a reboot the instances' containers are stopped: the agent removes them within seconds and players press Start again. Nothing to do by hand.
4. Agent or router misbehaving: `sudo docker restart l3mon-challenge-host-agent-1` (or `-router-1`). Instances survive an agent restart (rebuilt from labels) and a router restart (routes are polled again within a second).
5. Too many instances, host struggling: `POST /api/v1/l3mon/admin/instances/kill` kills them all (runbook 08); or lower `INSTANCER_MAX_SLOTS` / `INSTANCER_MAX_MEM_MB` in `/etc/l3mon/challenge-host.env` and recreate the agent.

## Players cannot start an instance

| What the player sees | Meaning | What to do |
|---|---|---|
| "No capacity" / retry in N s | the host is at its ceiling or working through a burst of starts | wait a few seconds; if it persists, check capacity; add a host or raise the ceiling |
| "The challenge servers are busy" | a start timed out (a burst) | usually clears by itself; the platform asked the host whether the start landed |
| "This challenge could not be started just now" | the container could not start (image, or the challenge crashes at start) | `sudo docker logs` of the agent on the host; a challenge that writes files into its own folder as a normal user needs the adaptation in the author checklist (section 4d) |
| Instance link answers 403 | the gate is in `crew` mode and the person is not signed in to Cloudflare Access | expected during a test round; at the event the gate must be `open` |
| Instance link answers 404 for a second or two after Start | the routers have not polled yet | normal; refresh |

## Adding a challenge

Images are built in CI and pulled by digest (never built on a challenge host). Run the author checklist (4d), put the stanza in the host's `challenges.toml` with the image digest, restart the agent, and prove it with the author's own reference solver against a live instance for two teams (each solve must return that team's flag): `services/instancer/tools/real_host_proof.py` shows how.

## Test round routine (what was done on 21 Nov 2026)

`bash services/instancer/tools/real_host_prepare.sh` then `python3 services/instancer/tools/real_host_proof.py ...` on the host: 106 checks plus the long ones (`--only expiry,capacity,leaks`). Run it again after any change to the instancer, the router, `bootstrap.sh` or the host's set-up.
