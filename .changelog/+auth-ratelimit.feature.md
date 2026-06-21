Add a token-bucket rate limiter on the authentication endpoints (NTRIP base/rover
auth and the RTCM session bootstrap), keyed by client IP, to throttle the
CPU-heavy bcrypt path. Configurable via `AUTH_RATELIMIT_ENABLED`,
`AUTH_RATELIMIT_CAPACITY`, and `AUTH_RATELIMIT_REFILL_PER_SECOND` (Helm:
`authRateLimit.*`); on by default.
