# Base-station authorization policy for CORSHub.
#
# A base station is identified solely by its username.  The username maps
# directly to a mountpoint by convention, so no separate mountpoint membership
# list is required: if the username exists in the registry the station is
# authorized to push corrections.
#
# Password verification is intentionally absent from this policy.  OPA has no
# bcrypt built-in, so the application layer retrieves `password_hash` and calls
# crypto.secrets.verify before trusting the `allow` decision.
#
# Input document expected by callers
# -----------------------------------
# {
#   "input": {
#     "username": string   // Basic-auth username
#   }
# }
#
# Result document returned at /v1/data/corshub/base_station
# ---------------------------------------------------------
# {
#   "allow":         bool,   // false when undefined (deny-by-default)
#   "password_hash": string  // absent when username is unknown
# }

package corshub.base_station

import rego.v1

default allow := false

# Grant access when the station exists in the registry.
allow if {
	data.corshub.base_stations[input.username]
}

# Expose the stored bcrypt hash so the caller can verify the supplied password.
# This rule is *undefined* (absent from the result) when the username is
# unknown, which the caller must treat as an authentication failure.
password_hash := data.corshub.base_stations[input.username].password_hash
