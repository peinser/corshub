package corshub.utils

import rego.v1

# Parse an ISO 8601 date string ("YYYY-MM-DD") to nanoseconds since the epoch.
date_ns(date) := time.parse_rfc3339_ns(concat("", [date, "T00:00:00Z"]))
