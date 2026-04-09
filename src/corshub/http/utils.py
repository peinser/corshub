r"""
HTTP utilities.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse


if TYPE_CHECKING:
    from typing import Final

    from sanic import Headers
    from sanic import Request


_PATCH_HEADERS: Final[set[str]] = {"authorization"}

_HEADER_REFERER: Final[str] = "referer"


def patch_through(headers: Headers) -> dict:
    patch_headers = {}

    for header in _PATCH_HEADERS:
        v = headers.get(header, None)
        if v:
            patch_headers[header] = v

    return patch_headers


def base_from_referer(request: Request) -> str | None:
    r"""
    Extract the base URL (scheme + netloc) from the referer header.

    Args:
        request: Sanic Request object containing headers

    Returns:
        str: Base URL (e.g., 'https://example.com') or None if referer is invalid/missing
    """
    referer = request.headers.get(_HEADER_REFERER)
    if not referer:
        return None

    try:
        parsed = urlparse(referer)

        return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None

    except ValueError:
        return None
