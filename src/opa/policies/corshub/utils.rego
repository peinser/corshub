package corshub.utils

import rego.v1

# Parse an ISO 8601 date string ("YYYY-MM-DD") to nanoseconds since the epoch.
# Undefined for unparseable input and for dates beyond 2262 (int64 nanoseconds).
date_ns(date) := time.parse_rfc3339_ns(concat("", [date, "T00:00:00Z"]))

# True when now lies inside the entry's optional validity window:
#   valid_from  - deny before this date (absent = no lower bound)
#   valid_until - deny on or after this date (absent = no upper bound)
# A bound that is present but unparseable denies (fail closed).
within_window(entry) if {
	now := time.now_ns()
	not _before_start(entry, now)
	not _after_end(entry, now)
}

# The comparisons live in their own rules on purpose: in `not now < date_ns(d)`
# OPA evaluates the call outside the negation, so an unparseable date would make
# the whole rule undefined and the window would silently pass (fail open).
_before_start(entry, now) if {
	entry.valid_from
	not _on_or_after(now, entry.valid_from)
}

_after_end(entry, now) if {
	entry.valid_until
	not _before(now, entry.valid_until)
}

_on_or_after(now, date) if now >= date_ns(date)

_before(now, date) if now < date_ns(date)
