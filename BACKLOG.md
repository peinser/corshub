# Backlog

Tracked bugs, quality issues, and planned features. Grouped by effort.

---

## Quick fixes

- [ ] `nearest.py`: `haversine(mp.latitude, mp.longitude, ...)` is called without a `None` guard. Both fields are `Optional[float]` on `Mountpoint`; a base station that connects without coordinates causes a `TypeError` for every rover requesting the nearest mountpoint. Filter out mountpoints with `None` coordinates before passing to haversine.
- [ ] `Mountpoint.__post_init__`: `if self.latitude and not -90 <= ...` skips validation when `latitude == 0.0` because `0.0` is falsy. The equator and prime meridian are valid coordinates. Change the guard to `if self.latitude is not None` (same fix needed for `longitude`).
- [ ] `NTRIPCaster._METADATA_FIELDS` is typed `Final[dict]` but is actually a `set`. Correct to `Final[set[str]]`.
- [ ] `http/security.py` contains a dead `protected` decorator. A leftover from an earlier iteration. The active one lives in `http/middleware.py`. Remove the dead copy to avoid confusion.
- [ ] `websocket.py` cleanup docstring references "chargepoint sessions", copied from an OCPP/EV-charging project. Fix the docstring.

---

## Medium effort

- [ ] Docker `validate` stage has `--cov` commented out of the pytest invocation, so the 75 % coverage threshold from `pyproject.toml` is never enforced in CI. Re-enable coverage in the Dockerfile.
- [ ] `NTRIPCasterCollector.collect()` directly accesses `caster._transports`, a private attribute. Add a public `transports` property on `NTRIPCaster` mirroring the existing `mountpoints` property.
- [ ] `sanic-testing` is pinned at 24.6 while Sanic is at 25.12. Verify API compatibility and update the pin.
- [ ] Dropped-frame counter: frames silently evicted by the bounded queue drop-oldest policy are not counted. Add a `ntrip_frames_dropped_total` counter (labels: `mountpoint`) incremented in `QueueTransportSubscriber.publish` on eviction and on the `QueueFull` fallback path. Expose in Grafana.
- [ ] `frame_interval_seconds` measures inter-arrival time at the caster using `time.monotonic()`, not end-to-end latency from GNSS generation. Extract the GNSS epoch timestamp from MSM frames (DF004 for GPS time-of-week, DF416 for GLONASS) and compute the delta against wall-clock UTC to get true generation-to-caster latency. Requires GPS time-of-week rollover and UTC offset handling.

---

## Medium-high

- [ ] Onboarding bot (`onboard.py`) verifies that only `ops/values.yaml` is touched but does not verify that only the submitter's own entry was modified. A malicious PR could alter another user's `mountpoints`, `valid_until`, or other fields. Fix: parse the before/after YAML and assert that only keys nested under the submitter's username changed.
- [ ] Graceful shutdown: `NTRIPCaster.stop()` only cancels the reaper task; it does not drain active rover connections or signal base stations. Decide on a drain timeout and propagate shutdown through all active transports.
- [ ] Add token bucket rate-limiter for auth endpoints based on connection fingerprint. `bcrypt.verify` is CPU-heavy and the lack of rate limiting makes it a viable DoS vector.

---

## Docker Compose deployment

For operators without Kubernetes infrastructure.

- [ ] Single `docker-compose.yml` (or `deploy/docker-compose.yml`) covering: caster, OPA sidecar, Prometheus, Grafana with the existing dashboard pre-provisioned.
- [ ] Env-file based configuration (`.env.example` committed, `.env` gitignored).
- [ ] OPA bundle loaded from a local `ops/values.yaml`-equivalent file mount — operators edit the file and restart OPA, no Helm required.
- [ ] Grafana datasource and dashboard provisioned automatically via volume mounts (reuse `grafana/corshub.json`).
- [ ] Reverse proxy (Caddy preferred for automatic TLS) terminating HTTPS for the public caster endpoint.
- [ ] Health checks on all services matching the existing liveness/readiness probe paths.
- [ ] README section documenting the Compose path as an alternative to Helm.

---

## Stream integrity and observability

- [ ] No session integrity model: there is no way to verify that what a rover received matches what the base station sent. A per-frame sequence number or rolling hash in the transport layer would make divergence detectable. -> With NATS we could enforce ordering on the topic, with the transport queue this is the case as well.
- [ ] No replay or verification layer: all RTCM data is ephemeral, making post-hoc debugging of bad surveys or spoofing incidents impossible. The attestation feature below addresses this. -> Also solved with NATS / Pulsar.

---

## Attestation (future feature)

Cryptographic proof of correction provenance and quality for surveyors who need to demonstrate data integrity for legal or regulatory purposes.

Design sketch:

- Rolling hash chain per mountpoint: `H(prev_hash || timestamp || mountpoint || sha256(frame) || arp_xyz || sat_count)`, one block per frame (or per time window).
- Each block signed with a server-held Ed25519 key.
- Store only hashes server-side — not raw RTCM frames (data volume and data-ownership concerns).
- Append-only SQLite WAL log per caster instance, with periodic rotation and archival.
- `GET /api/v1/attest?mountpoint=X&from=T1&to=T2` returns a signed JSON document: hash chain segment, metadata window (ARP position, CNR summary, satellite geometry), and the server public key for independent verification.
- A surveyor who recorded raw RTCM locally reproduces the hashes to verify "these are the exact bytes I received from this mountpoint during this window."
- `_observe_rtcm_quality` already provides all needed signals (ARP, CNR, sat count); the attestation layer sits on top of the same parse path.
- Legal framing: the attestation proves data integrity and signal quality, not survey accuracy. Document this distinction clearly to avoid liability for downstream survey errors.
