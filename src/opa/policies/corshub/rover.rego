# Rover authorization policy for CORSHub.
#
# A rover may subscribe to a mountpoint when ALL of the following conditions
# hold:
#
#   1. The username exists in `data.corshub.rovers`.
#   2. The rover's `mountpoints` list contains either the requested mountpoint
#      or the wildcard `"*"` (grants access to every mountpoint).
#   3. The current time falls within the rover's validity window.
#
# Validity window fields (both optional, ISO 8601 date "YYYY-MM-DD"):
#   valid_from  — deny connections before this date (absent = no lower bound)
#   valid_until — deny connections on or after this date (absent = no upper bound)
#
# As with base stations, password verification is delegated to the application
# layer (bcrypt.checkpw).  OPA exposes `password_hash` for that purpose.
#
# Input document expected by callers
# -----------------------------------
# {
#   "input": {
#     "username":   string,  // Basic-auth username
#     "mountpoint": string   // URL path segment the rover is subscribing to
#   }
# }
#
# Result document returned at /v1/data/corshub/rover
# --------------------------------------------------
# {
#   "allow":               bool,      // false when undefined (deny-by-default)
#   "password_hash":       string     // absent when username is unknown
#   "max_session_seconds": int        // Maximum number of seconds the session can take, if allowed, <= 0 for infinite. For users where this field is not applicable, it will not be present.
# }

package corshub.rover

import rego.v1

default allow := false

rover := data.corshub.rovers[input.username]

# Resolve valid_from to nanoseconds; default to the epoch (open lower bound).
_from_ns := time.parse_rfc3339_ns(concat("", [rover.valid_from, "T00:00:00Z"])) if rover.valid_from

_from_ns := 0 if not rover.valid_from

# Resolve valid_until to nanoseconds; default to year 9999 (open upper bound).
_until_ns := time.parse_rfc3339_ns(concat("", [rover.valid_until, "T00:00:00Z"])) if rover.valid_until

_until_ns := 253402300800000000000 if not rover.valid_until

# Shared validity check used by both allow rules below.
_within_window if {
	now := time.now_ns()
	now >= _from_ns
	now < _until_ns
}

# Explicit mountpoint match.
allow if {
	rover
	input.mountpoint in rover.mountpoints
	_within_window
}

# Wildcard: rover is granted access to every mountpoint.
allow if {
	rover
	"*" in rover.mountpoints
	_within_window
}

# Expose the stored bcrypt hash for application-layer password verification.
# Undefined (absent) when the username is unknown.
password_hash := data.corshub.rovers[input.username].password_hash

# The `anonymous` user has a limited session duration.
max_session_seconds := 60 if input.username == "anonymous"
