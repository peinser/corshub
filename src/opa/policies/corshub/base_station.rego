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
# Validity window fields (both optional, ISO 8601 date "YYYY-MM-DD"):
#   valid_from  — deny connections before this date (absent = no lower bound)
#   valid_until — deny connections on or after this date (absent = no upper bound)
#
# Input document expected by callers
# -----------------------------------
# {
#   "input": {
#     "username":  string,  // Basic-auth username
#     "mountpoint": string, // Mountpoint the station is publishing to
#     "transport": {
#       "available": boolean  // true if a transport is already allocated
#     }
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

import data.corshub.utils

default allow := false

# Grant access when the station exists, the mountpoint matches, no transport is
# already active, and the current time falls within the validity window.
allow if {
	s := data.corshub.base_stations[input.username]
	s.mountpoint == input.mountpoint
	input.transport.available == false
	now := time.now_ns()
	now >= utils.date_ns(object.get(s, "valid_from", "1970-01-01"))
	now < utils.date_ns(object.get(s, "valid_until", "9999-12-31"))
}

# Expose the stored bcrypt hash so the caller can verify the supplied password.
# Undefined (absent) when the username is unknown.
password_hash := data.corshub.base_stations[input.username].password_hash
