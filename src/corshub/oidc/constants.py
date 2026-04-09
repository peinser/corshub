r"""
Utilities and constants related to the Open ID Connect protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from typing import Final


ACCESS_TOKEN: Final[str] = "access_token"
AUD: Final[str] = "aud"
CLIENT_ID: Final[str] = "client_id"
CODE: Final[str] = "code"
COOKIE_ACCESS_TOKEN: Final[str] = f"corshub_{ACCESS_TOKEN}"
COOKIE_AUD = "corshub_aud"  # We use this Cookie to identify the correct JWK provider.
ID_TOKEN: Final[str] = "id_token"
COOKIE_ID_TOKEN: Final[str] = f"corshub_{ID_TOKEN}"
REFRESH_TOKEN: Final[str] = "refresh_token"
COOKIE_REFRESH_TOKEN: Final[str] = f"corshub_{REFRESH_TOKEN}"
EXPIRES_IN: Final[str] = "expires_in"
HEADER_HINT_ISSUER: Final[str] = "Issuer"
ISS: Final[str] = "iss"
JWKS: Final[str] = "jwks"
KID: Final[str] = "kid"
STATE: Final[str] = "state"
EXP: Final[str] = "exp"
GRANT_TYPE: Final[str] = "grant_type"

DEFAULT_REFRESH_TOKEN_MAX_AGE: Final[int] = 604800  # 7 days
