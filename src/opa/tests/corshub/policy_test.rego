# Tests for the CORSHub authorization policies.  Run with `make policy-test`.
#
# Kept outside src/opa/policies/ so they are never shipped in the Helm chart.
package corshub.policy_test

import rego.v1

import data.corshub.base_station
import data.corshub.rover

# 2026-06-01T00:00:00Z
now := 1780272000000000000

stations := {
	"open": {"mountpoint": "OPEN", "password_hash": "h"},
	"windowed": {"mountpoint": "WIN", "password_hash": "h", "valid_from": "2026-01-01", "valid_until": "2026-06-01"},
	"future": {"mountpoint": "FUT", "password_hash": "h", "valid_from": "2026-06-02"},
	"garbled": {"mountpoint": "BAD", "password_hash": "h", "valid_until": "sometime"},
	"vetted": {"mountpoint": "VET", "password_hash": "h", "verified": true},
	"stringly": {"mountpoint": "STR", "password_hash": "h", "verified": "true"},
}

rovers := {
	"anonymous": {"mountpoints": ["*"], "password_hash": "h"},
	"pinned": {"mountpoints": ["OPEN"], "password_hash": "h", "valid_until": "2027-01-01"},
	"garbled": {"mountpoints": ["*"], "password_hash": "h", "valid_from": "01/01/2026"},
}

station(username, mountpoint) := {"username": username, "mountpoint": mountpoint, "transport": {"available": false}}

# -- base stations --------------------------------------------------------------

test_station_without_dates_is_allowed if {
	base_station.allow with input as station("open", "OPEN") with data.corshub.base_stations as stations with time.now_ns as now
}

test_station_denied_on_valid_until_day if {
	not base_station.allow with input as station("windowed", "WIN") with data.corshub.base_stations as stations with time.now_ns as now
}

test_station_denied_before_valid_from if {
	not base_station.allow with input as station("future", "FUT") with data.corshub.base_stations as stations with time.now_ns as now
}

test_station_with_unparseable_date_is_denied if {
	not base_station.allow with input as station("garbled", "BAD") with data.corshub.base_stations as stations with time.now_ns as now
}

test_station_denied_on_other_mountpoint if {
	not base_station.allow with input as station("open", "VET") with data.corshub.base_stations as stations with time.now_ns as now
}

test_station_denied_while_mountpoint_is_live if {
	live := object.union(station("open", "OPEN"), {"transport": {"available": true}})
	not base_station.allow with input as live with data.corshub.base_stations as stations with time.now_ns as now
}

test_verified_only_for_boolean_true if {
	base_station.verified with input as station("vetted", "VET") with data.corshub.base_stations as stations
	not base_station.verified with input as station("open", "OPEN") with data.corshub.base_stations as stations
	not base_station.verified with input as station("stringly", "STR") with data.corshub.base_stations as stations
}

test_verified_absent_for_unknown_station if {
	r := base_station with input as station("nobody", "X") with data.corshub.base_stations as stations
	not "verified" in object.keys(r)
}

# -- rovers ---------------------------------------------------------------------

test_wildcard_rover_without_dates_is_allowed if {
	rover.allow with input as {"username": "anonymous", "mountpoint": "ANY"} with data.corshub.rovers as rovers with time.now_ns as now
}

test_pinned_rover_only_on_its_mountpoint if {
	rover.allow with input as {"username": "pinned", "mountpoint": "OPEN"} with data.corshub.rovers as rovers with time.now_ns as now
	not rover.allow with input as {"username": "pinned", "mountpoint": "VET"} with data.corshub.rovers as rovers with time.now_ns as now
}

test_rover_with_unparseable_date_is_denied if {
	not rover.allow with input as {"username": "garbled", "mountpoint": "ANY"} with data.corshub.rovers as rovers with time.now_ns as now
}

test_unknown_rover_is_denied if {
	not rover.allow with input as {"username": "nobody", "mountpoint": "ANY"} with data.corshub.rovers as rovers with time.now_ns as now
}

test_only_anonymous_has_a_session_limit if {
	rover.max_session_seconds == 60 with input as {"username": "anonymous", "mountpoint": "ANY"} with data.corshub.rovers as rovers
	not rover.max_session_seconds with input as {"username": "pinned", "mountpoint": "OPEN"} with data.corshub.rovers as rovers
}
