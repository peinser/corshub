# Direct-to-Rover RTCM over UDP

| | |
|---|---|
| **Status** | Implemented (caster side); rover daemon is external |
| **Caster modules** | `corshub.rtcm` (udp, keys, tokens), `corshub.services.v1.rtcm` (endpoints) |
| **Availability** | Optional, opt-in - disabled by default (`RTCM_UDP_ENABLED=false`) |
| **Scope** | Caster (this repo) + rover-side daemon (separate repo) |
| **Wire contract** | `proto/corshub/rtcm/v1/rtcm_udp.proto` |
| **Transport** | Authenticated, rover-initiated UDP |
| **Integrity** | Optional Ed25519 (publish-key, asymmetric); on in production, off for dev/integration |

## Summary

A second correction egress alongside the existing NTRIP/HTTP path: the caster
pushes **raw RTCM frames as signed UDP datagrams** straight to an internet-connected
rover (drone). The rover-side daemon verifies each frame against the caster's
**published Ed25519 public key**, then re-encodes it as MAVLink `GPS_RTCM_DATA`
and hands it to the flight controller over a local loopback link (mavlink-router) or UART.

This removes the `caster -> GCS -> telemetry radio -> drone` relay and replaces
head-of-line-blocking TCP with a drop-and-supersede datagram stream, which suits
a lossy cellular link carrying corrections that are only useful for ~1-2 seconds.

## Optionality

Both the feature and its signing are settings; CORSHub's existing behaviour is
unchanged unless you opt in.

- **The UDP egress is opt-in and off by default** (`RTCM_UDP_ENABLED=false`). When
  disabled, none of this is wired up - no UDP socket is bound and no bootstrap or
  public-key route is registered. The caster runs exactly as it does today, NTRIP
  over HTTP only.
- **Signing is independently optional** (`RTCM_UDP_SIGNING_ENABLED`). Turn it on in
  production so rovers can verify provenance against the published key. Turning it
  off sends frames in the clear - a deliberate convenience for **development and
  integration testing**, where standing up an Ed25519 key and pinning fingerprints
  is needless friction. When signing is off the rover daemon runs with verification
  off to match, and `SignedCorrection.signature` is left empty. Do not run unsigned
  in production: an unauthenticated frame stream into a flight controller is exactly
  the spoofing vector the caster exists to flag.

## Motivation

- **Latency / path.** The classic path routes corrections through the GCS and a
  constrained telemetry radio. A drone with its own internet link can receive
  corrections directly, cutting the relay and the radio bottleneck.
- **Wrong transport for the job.** RTCM corrections are time-critical and
  short-lived. TCP's head-of-line blocking retransmits *stale* corrections -
  reliably delivering data that is already useless on arrival. The correct
  semantic on a flaky link is **drop-and-supersede**: lose an epoch, the next
  1 Hz epoch replaces it. That is a datagram. The caster already embodies this
  with its drop-oldest per-subscriber queue.
- **Open verification.** On a public caster, unauthenticated RTCM injected into a
  flight controller is a spoofing vector - the inverse of what the caster exists
  to detect. Frames must be verifiable. MAVLink 2 signing is symmetric HMAC with
  a shared secret (no public key), so it cannot offer "publish a key, anyone
  verifies." An application-layer Ed25519 signature can, and is what we use.

## Goals / Non-goals

**Goals**

- Direct caster-to-rover RTCM delivery over UDP, no GCS in the path.
- Optional per-frame authenticity and provenance via a published Ed25519 key.
- One UDP listener for all mountpoints; mountpoint chosen per session.
- Survive carrier-grade NAT (CGNAT) port rebinding.
- Reuse the existing auth (OPA + bcrypt) and the existing transport subscriber.
- Be inert unless explicitly enabled (`RTCM_UDP_ENABLED=false` by default).

**Non-goals**

- The caster is **not** MAVLink-aware. It emits raw RTCM; MAVLink encoding lives
  in the rover daemon.
- No confidentiality. RTCM corrections are not secret; we sign, we do not encrypt.
  (Tokens and any secret material are delivered over the HTTPS bootstrap, not UDP.)
- No reliability/retransmission. Loss is handled by supersession, not recovery.
- FC-side signature enforcement is out of scope; the daemon is the verifier.

## Trust model

The path splits into two legs with different trust:

```
  UNTRUSTED (public internet, spoofable)        TRUSTED (host-local)
  ───────────────────────────────────────   ───────────────────────────
  caster  ──signed UDP (Ed25519)──▶  daemon ──GPS_RTCM_DATA (plain)──▶ FC
                                       │            via mavlink-router
                                       └─ verifies signature, drops forgeries
```

- **caster → daemon** is the only leg an attacker can reach, so it is the only
  leg that is signed and verified.
- **daemon → flight controller** is loopback/serial on the same host: trusted,
  unsigned, plain MAVLink. This is why no MAVLink-layer signing is needed and the
  mavlink-router signing/hostname questions are moot.

## Components

| Component | Repo | Responsibility |
|---|---|---|
| Bootstrap endpoint | this | `POST /api/v1/rtcm/session`: Basic auth → OPA → bcrypt, issues a session JWT |
| Public-key endpoint | this | `GET /api/v1/rtcm/jwks.json`: serves the Ed25519 signing key as a JWKS |
| UDP egress server | this | `asyncio` datagram server: sessions, signing, fan-out |
| Signing key | this | Ed25519 private key, loaded from config (ephemeral + loud warning in dev) |
| Rover daemon (UDP service) | external | HELLO/keepalive, verify, MAVLink encode, loopback to mavlink-router |

The UDP egress is a **new consumer of the existing transport**: per session it
does `async with caster.subscribe(mountpoint) as sub:` and loops on `sub.get()`,
inheriting the drop-oldest freshness semantics unchanged. No core caster change.

## Protocol

Transport: UDP. **Every packet is exactly one `Datagram`** (see the proto). The
`Datagram` is the unsigned outer envelope (version, `session_id`, `seq`, body);
authenticity lives in the signed inner `CorrectionFrame`.

### 1. Bootstrap (HTTPS, keeps bcrypt off UDP)

Putting bcrypt behind an unauthenticated UDP packet would be a DoS amplifier, so
authentication happens over the existing TLS HTTP surface and yields a cheap token.

`POST /api/v1/rtcm/session` - `Authorization: Basic <user:pass>`

```json
// 200 OK
{
  "token": "<JWT, HS256, aud=rtcm-udp, claims: mountpoint, exp ~60s>",
  "udp_endpoint": "corshub.peinser.com:5009",
  "signing_kid": "Vh9..._g"
}
```

The Basic credentials run through the same `authenticate_rover` / OPA / bcrypt
path as the NTRIP route. The JWT binds the authorized mountpoint and a short
expiry; the UDP server verifies it with an HMAC secret (no bcrypt, no DB).

### 2. Session establishment

```
rover  ──Datagram{ session_id=0, body=Hello{ token, mountpoint|"NEAREST", position? } }──▶  caster
rover  ◀──Datagram{ session_id=S, body=HelloAck{ session_id=S, mountpoint, signing_kid,
                                                 keepalive_interval_s, session_ttl_s } }──  caster
```

The caster verifies the token, resolves the mountpoint (`NEAREST` → concrete via
the same haversine/mask logic as the `/NEAREST` route), subscribes, assigns a
random 64-bit `session_id`, and records `session_id → {subscriber, last_addr, expiry}`.

### 3. Correction stream (sign once, fan out)

For each RTCM frame published on a mountpoint, the caster builds **one**
`CorrectionFrame`, serializes it, signs those bytes once, and wraps the resulting
`SignedCorrection` into a per-recipient `Datagram` (only `session_id`/`seq` differ):

```
caster ──Datagram{ session_id=S, seq=n, body=SignedCorrection{ payload, signature } }──▶ rover
```

The rover: verify `signature` over `payload` with the pinned public key → parse
`CorrectionFrame` → reject if `timestamp_ms` is outside the acceptance window →
drop if `seq` is older than the newest seen → emit `GPS_RTCM_DATA`.

### 4. Keepalive, handoff, teardown

- `KeepAlive` every `keepalive_interval_s` holds the NAT mapping open and proves
  liveness. It MAY carry a position; on a `NEAREST` session that drives dynamic
  base-station handoff.
- `SwitchMountpoint` re-points the session's subscription in place.
- `Bye` (either direction) tears down. The caster also expires a session after
  `session_ttl_s` of silence, unsubscribing and clearing any tracked position.

### NAT / CGNAT handling

The caster **demultiplexes inbound datagrams by `session_id`, never by UDP
5-tuple.** Cellular CGNAT silently rebinds the public source port mid-session; on
any authenticated datagram from a known `session_id`, the caster updates
`last_addr` and keeps sending there. (This is exactly what QUIC connection IDs
do; we hand-roll the minimum needed for plain UDP. A future QUIC migration is a
natural evolution - see Future work.)

### Complete flow

```mermaid
sequenceDiagram
    participant R as Rover daemon
    participant H as Caster HTTPS
    participant U as Caster UDP egress
    participant T as Transport (mountpoint)
    participant FC as Flight controller

    Note over R,H: Bootstrap (TLS, authenticated)
    R->>H: POST /api/v1/rtcm/session (Basic auth)
    H->>H: OPA policy + bcrypt verify
    H-->>R: { token (JWT), udp_endpoint, signing_kid }
    R->>H: GET /api/v1/rtcm/jwks.json
    H-->>R: JWKS (OKP/Ed25519); select + pin by kid

    Note over R,U: Session (UDP, one well-known port)
    R->>U: Hello{ token, mountpoint, position? } (session_id=0)
    U->>U: verify JWT, resolve mountpoint
    U->>T: subscribe(mountpoint)
    U-->>R: HelloAck{ session_id=S, mountpoint, signing_kid, ttl }

    Note over T,FC: Correction stream
    loop per RTCM frame (~1 Hz)
        T-->>U: frame
        U->>U: build+serialize CorrectionFrame, sign once (Ed25519)
        U-->>R: SignedCorrection{ payload, signature } (seq=n)
        R->>R: verify sig, check timestamp window, drop-stale by seq
        R->>FC: GPS_RTCM_DATA (mavlink-router, loopback)
    end

    loop every keepalive_interval_s
        R->>U: KeepAlive{ position? }  (refresh NAT, liveness, NEAREST handoff)
    end

    R->>U: Bye  (or caster expires after session_ttl_s)
    U->>T: unsubscribe
```

## Signing and key management

- **Algorithm:** Ed25519. Sign/verify helpers added to
  `crypto/sign.py` (today HMAC-only); adds a
  `cryptography` dependency.
- **Signed bytes:** the opaque serialized `CorrectionFrame` (`SignedCorrection.payload`).
  Verify over the received bytes, then parse. Never re-serialize before verifying -
  proto3 output is not guaranteed canonical across implementations.
- **What the signature binds:** `timestamp_ms` (freshness/replay window) and
  `mountpoint` (a frame cannot be replayed as another mountpoint's), plus the RTCM
  bytes.
- **Optional (`RTCM_UDP_SIGNING_ENABLED`).** On in production; off is a
  development / integration-testing convenience (see [Optionality](#optionality)).
  When off, frames are sent with an empty `SignedCorrection.signature` and the
  daemon verifies nothing - never in production.
- **Key provisioning (two supported forms, pick either):**
    - *File mount* - `RTCM_SIGNING_KEY_PATH` points at the private-key PEM (e.g. a
      mounted Kubernetes Secret), optionally with `RTCM_SIGNING_PUBKEY_PATH` for
      the public-key PEM.
    - *Inline config* - `RTCM_SIGNING_PRIVATE_KEY` carries the key material
      directly (PEM or base64 seed), optionally with `RTCM_SIGNING_PUBLIC_KEY`.

  Inline values take precedence over file paths. The **public key is derivable
  from the private key**, so supplying it is optional; when supplied it is verified
  to match and the caster fails fast on mismatch. If no key is configured in a dev
  environment, generate an ephemeral key and log a prominent warning that it is
  non-persistent and unpinnable; never auto-generate in production.
- **Replay window:** reject `CorrectionFrame.timestamp_ms` outside ±5 s (tunable).
  A captured frame is replayable only within the window, where it is a genuine,
  recent correction (drop-and-supersede makes that near-harmless). Forging a *new*
  correction requires the private key.

### Public-key endpoint (JWKS)

The caster publishes its signing public key as a standard **JWKS** so any party
can verify a `CorrectionFrame` independently - no shared secret, nothing to
provision, and using a format every ecosystem already has tooling for.

`GET /api/v1/rtcm/jwks.json`

```json
// 200 OK  (application/jwk-set+json)
{
  "keys": [
    {
      "kty": "OKP",
      "crv": "Ed25519",
      "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",  // base64url raw public key
      "kid": "Vh9..._g",                                     // RFC 7638 JWK thumbprint
      "use": "sig",
      "alg": "EdDSA"
    }
  ]
}
```

The daemon fetches the set, selects the key whose `kid` matches the `signing_kid`
advertised in `HelloAck`, and verifies with it - exactly the lookup-by-`kid`
pattern `jwt.py`'s `JWKSManager` already uses for external issuers. The route is
registered only when the feature is enabled and returns `404` when signing is
disabled (no key to publish).

Reuse notes:
- **Serving side:** pyjwt's `OKPAlgorithm.to_jwk()` produces the Ed25519 JWK; the
  `kid` is the RFC 7638 thumbprint, so it is deterministic and self-certifying.
- **Consuming side:** `JWKSManager` is RS256/RSA-only today, so add `EdDSA`/`OKP`
  support (a few lines via `OKPAlgorithm.from_jwk`) if you want to reuse it
  in-process; external daemons use any standard JWKS library.

> **This is not an identity surface.** CORSHub remains an OIDC *consumer*, not a
> *provider*: `jwt.py` fetches issuers' JWKS to verify their tokens. This endpoint
> serves a single signing key in JWKS form purely for tooling compatibility - it is
> deliberately *not* paired with an OIDC discovery document
> (`.well-known/openid-configuration`), which would be overkill for one key.

## Caster integration

- **Lifecycle:** bind the UDP socket and start the server in a
  `before_server_start` listener; close it and drain sessions in
  `after_server_stop` - mirroring the reaper and quality-worker lifecycle on
  `NTRIPCaster`.
- **Egress task per session:** `subscribe(mountpoint)` → loop `frame = await sub.get()`
  → `sendto(datagram, session.last_addr)`. Reuses `QueueTransportSubscriber`
  drop-oldest, so a slow/lost rover never backs up the publisher.
- **Sign-once cache:** sign each frame once per mountpoint and reuse the
  `SignedCorrection` bytes across all sessions on that mountpoint; only the
  enclosing `Datagram` differs per recipient.

## Configuration

All via `env.extract` (`src/corshub/env.py`), with dev-safe defaults.

| Variable | Default | Meaning |
|---|---|---|
| `RTCM_UDP_ENABLED` | `false` | Master switch for the UDP egress |
| `RTCM_UDP_HOST` | `0.0.0.0` | Bind address |
| `RTCM_UDP_PORT` | `5009` | Single listener port for all mountpoints |
| `RTCM_UDP_ENDPOINT` | `host:port` | Public endpoint advertised to rovers in the bootstrap response |
| `RTCM_UDP_SIGNING_ENABLED` | `false` | Sign outgoing frames (off only in dev) |
| `RTCM_SIGNING_ALLOW_EPHEMERAL` | `false` | Allow a non-persistent ephemeral key when none is supplied (dev only) |
| `RTCM_SIGNING_KEY_PATH` | - | Path to Ed25519 private key (PEM); e.g. a mounted secret |
| `RTCM_SIGNING_PUBKEY_PATH` | - | Optional path to public key (PEM); derived from private if unset |
| `RTCM_SIGNING_PRIVATE_KEY` | - | Inline private key (PEM/base64); takes precedence over the path |
| `RTCM_SIGNING_PUBLIC_KEY` | - | Optional inline public key; verified against the private key |
| `RTCM_UDP_SESSION_TTL` | `30` | Idle seconds before a session is reaped |
| `RTCM_UDP_KEEPALIVE_INTERVAL` | `10` | Advertised keepalive interval (s) |
| `RTCM_UDP_MAX_DATAGRAM` | `1200` | Max datagram bytes (under IPv6 min MTU) |
| `RTCM_SESSION_TOKEN_SECRET` | - | HS256 secret for the bootstrap JWT (>= 32 bytes; required when enabled) |
| `RTCM_SESSION_TOKEN_TTL` | `60` | Bootstrap token lifetime (s) |

Notes: the public key is always derived from the private key, so
`RTCM_SIGNING_PUBKEY_PATH` / `RTCM_SIGNING_PUBLIC_KEY` are reserved and not
required. `RTCM_UDP_MAX_DATAGRAM` is enforced: datagrams larger than the cap are
dropped (counted by `rtcm_udp_oversize_dropped_total`) rather than risk IP
fragmentation on a lossy link.

### Helm

The whole feature is driven by the Helm chart under the `rtcmUdp` key - enable
with `rtcmUdp.enabled=true`. The chart renders the env above onto the Deployment,
opens the container UDP port, creates a dedicated UDP `Service`
(`rtcmUdp.service`), and manages the token secret + signing key as a `Secret`
(generated and preserved across upgrades, or `rtcmUdp.secret.existingSecret`).
When disabled (the default), none of those resources are rendered.

## Protobuf contract and codegen

- Contract lives at `proto/corshub/rtcm/v1/rtcm_udp.proto`
  and is the single source of truth for both repos.
- Manage with [`buf`](https://buf.build) (lint + breaking-change checks in CI).
- Caster: add the `protobuf` runtime dependency; generate Python stubs at build
  time. The daemon generates stubs for its own language from the same file.

## Failure modes and edge cases

- **Datagram > MTU.** Frames near the 1029 B RTCM max plus envelope stay under
  the 1200 B cap → no IP fragmentation on lossy links. A frame that would exceed
  the cap is dropped and counted (should not occur with standard MSM output).
- **Reordering.** UDP may reorder; the receiver uses `seq` to drop stale frames.
- **Token expiry.** A `Hello` with an expired/invalid JWT gets `Error`; the rover
  re-runs bootstrap. The data stream itself needs no re-auth (session-scoped).
- **Mountpoint goes offline.** The subscriber's stream ends; the caster sends
  `Bye{reason}` and reaps the session.
- **Key rotation.** The JWKS can carry current+previous keys with distinct `kid`s;
  the daemon selects by the `kid` advertised in `HelloAck`, so rotation is a JWKS
  publish plus a `kid` switch. Rotate loudly and keep the old key in the set during a
  grace window.
- **DoS.** UDP `Hello` floods only cost a JWT verification (cheap, constant time),
  never bcrypt. Rate-limit `Hello` per source address.

## Observability

New metrics (Prometheus), following the existing `ntrip_*` conventions:

- `rtcm_udp_sessions` (gauge, per mountpoint)
- `rtcm_udp_datagrams_sent_total` / `_bytes_sent_total` (counter, per mountpoint)
- `rtcm_udp_frames_signed_total` and signing latency histogram
- `rtcm_udp_hello_total{result}` and `rtcm_udp_token_rejected_total`
- `rtcm_udp_session_reaped_total{reason}`

## Open questions

- Token: HS256 with a server secret (proposed, simplest - caster both issues and
  verifies) vs. reusing an asymmetric key.
- `session_id` width: 64-bit random (proposed). Sufficient against guessing given
  the token gate and short session lifetimes.
- Acceptance window (±5 s), keepalive (10 s), TTL (30 s) - confirm against real
  cellular RTT and NAT timeout behaviour.

## Future work

- **QUIC migration.** QUIC gives connection IDs (NAT survival), TLS auth, and RFC
  9221 unreliable datagrams natively - superseding the hand-rolled session/NAT
  layer here while keeping the Ed25519 frame signature for end-to-end provenance.
- **Continuous mask enforcement** (disconnect a rover that roams out of a
  mountpoint's configured range). NEAREST handoff on the `KeepAlive` position
  feed is implemented; mask enforcement is the remaining half.
- **Attestation tie-in.** The signed `CorrectionFrame` stream is the same
  provenance primitive as the attestation feature in `BACKLOG.md`; a hash chain
  over `CorrectionFrame`s would yield a verifiable record for surveyors.
