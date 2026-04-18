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

default allow := false

station := data.corshub.base_stations[input.username]

# Resolve valid_from to nanoseconds; default to the epoch (open lower bound).
_from_ns := time.parse_rfc3339_ns(concat("", [station.valid_from, "T00:00:00Z"])) if station.valid_from

_from_ns := 0 if not station.valid_from

# Resolve valid_until to nanoseconds; default to year 9999 (open upper bound).
_until_ns := time.parse_rfc3339_ns(concat("", [station.valid_until, "T00:00:00Z"])) if station.valid_until

_until_ns := 253402300800000000000 if not station.valid_until

# Grant access when the station exists, the mountpoint matches, no transport is
# already active, and the current time falls within the validity window.
allow if {
	station
	station.mountpoint == input.mountpoint
	input.transport.available == false
	now := time.now_ns()
	now >= _from_ns
	now < _until_ns
}

# Expose the stored bcrypt hash so the caller can verify the supplied password.
# Undefined (absent) when the username is unknown.
password_hash := station.password_hash
