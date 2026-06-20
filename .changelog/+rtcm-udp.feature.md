Add an optional direct-to-rover RTCM correction egress over signed UDP. Rovers
authenticate once over HTTPS (`POST /api/v1/rtcm/session`), then receive raw RTCM
frames as Ed25519-signed UDP datagrams, demultiplexed per session so they survive
carrier-grade NAT rebinding. The signing public key is published as a JWKS at
`GET /api/v1/rtcm/jwks.json`. Disabled by default (`RTCM_UDP_ENABLED=false`);
signing is independently optional. See `docs/architecture/rtcm-udp.md`.
