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
#   "allow":               bool,   // false when undefined (deny-by-default)
#   "password_hash":       string  // absent when username is unknown
#   "max_session_seconds": int     // absent for unlimited sessions
# }

package corshub.rover

import rego.v1

import data.corshub.utils

default allow := false

_within_window(r) if {
	from_ns := utils.date_ns(object.get(r, "valid_from", "1970-01-01"))
	until_ns := utils.date_ns(object.get(r, "valid_until", "9999-12-31"))
	now := time.now_ns()
	now >= from_ns
	now < until_ns
}

# Explicit mountpoint match.
allow if {
	r := data.corshub.rovers[input.username]
	input.mountpoint in r.mountpoints
	_within_window(r)
}

# Wildcard: rover is granted access to every mountpoint.
allow if {
	r := data.corshub.rovers[input.username]
	"*" in r.mountpoints
	_within_window(r)
}

# Expose the stored bcrypt hash for application-layer password verification.
# Undefined (absent) when the username is unknown.
password_hash := data.corshub.rovers[input.username].password_hash

# The `anonymous` user has a limited session duration.
max_session_seconds := 60 if input.username == "anonymous"
