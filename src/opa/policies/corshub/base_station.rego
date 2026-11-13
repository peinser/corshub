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
# `verified` (optional, default false) is set by a maintainer once a station has
# been vetted; the onboarding bot can never set it.  Only verified stations are
# eligible for automatic (NEAREST) routing; unverified ones serve only rovers that
# ask for them by name.
#
# Validity window fields (both optional, ISO 8601 date "YYYY-MM-DD"):
#   valid_from  - deny connections before this date (absent = no lower bound)
#   valid_until - deny connections on or after this date (absent = no upper bound)
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
#   "password_hash": string, // absent when username is unknown
#   "verified":      bool    // absent when username is unknown
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
	utils.within_window(s)
}

# Expose the stored bcrypt hash so the caller can verify the supplied password.
# Undefined (absent) when the username is unknown.
password_hash := data.corshub.base_stations[input.username].password_hash

# True only for an explicit boolean `verified: true` in the registry entry.
verified := object.get(data.corshub.base_stations[input.username], "verified", false) == true
