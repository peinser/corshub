# Rover authorization policy for CORSHub.
#
# A rover may subscribe to a mountpoint when ALL of the following conditions
# hold:
#
#   1. The username exists in `data.corshub.rovers`.
#   2. The rover's `mountpoints` list contains either the requested mountpoint
#      or the wildcard `"*"` (grants access to every mountpoint).
#
# As with base stations, password verification is delegated to the application
# layer (bcrypt.checkpw).  OPA exposes `password_hash` for that purpose.
#
# Input document expected by callers
# -----------------------------------
# {
#   "input": {
#     "username":   string,   // Basic-auth username
#     "mountpoint": string    // URL path segment the rover is subscribing to
#   }
# }
#
# Result document returned at /v1/data/corshub/rover
# --------------------------------------------------
# {
#   "allow":         bool,   // false when undefined (deny-by-default)
#   "password_hash": string  // absent when username is unknown
# }

package corshub.rover

import rego.v1

default allow := false

# Explicit mountpoint match.
allow if {
	rover := data.corshub.rovers[input.username]
	input.mountpoint in rover.mountpoints
}

# Wildcard: rover is granted access to every mountpoint.
allow if {
	rover := data.corshub.rovers[input.username]
	"*" in rover.mountpoints
}

# Expose the stored bcrypt hash for application-layer password verification.
# Undefined (absent) when the username is unknown.
password_hash := data.corshub.rovers[input.username].password_hash
