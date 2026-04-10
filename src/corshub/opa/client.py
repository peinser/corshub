"""
Async OPA (Open Policy Agent) REST client.

A thin, domain-agnostic wrapper around OPA's ``/v1/data`` HTTP endpoint.
Callers are responsible for interpreting the result document and for any
additional verification steps (e.g. password checking) that OPA cannot perform.

Fail-closed contract
--------------------
Any network error, timeout, or unexpected HTTP status causes ``query`` to
return an empty dict.  Callers that treat a missing ``allow`` key as ``False``
therefore deny access automatically when OPA is unreachable, which is the safe
default for a security-critical sidecar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp

from corshub.logging import logger


if TYPE_CHECKING:
    from typing import Any


# Per-request timeout so a slow OPA does not stall the event loop indefinitely.
_TIMEOUT_SECONDS: float = 5.0


class OPAClient:
    """Async REST client for OPA's ``/v1/data`` endpoint.

    Args:
        base_url: Root URL of the OPA server, e.g. ``"http://opa:8181"``.
        session:  Shared :class:`aiohttp.ClientSession` (owned by the caller).
    """

    def __init__(self, base_url: str, session: aiohttp.ClientSession) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session

    async def query(self, package: str, input_data: dict[str, Any]) -> dict[str, Any]:
        """POST an input document to ``/v1/data/<package>`` and return the result.

        Args:
            package:    Slash-separated OPA package path, e.g.
                        ``"corshub/base_station"``.
            input_data: Arbitrary JSON-serialisable dict sent as ``input`` to
                        the policy.

        Returns:
            The ``result`` object from OPA's response, or an empty dict if OPA
            is unreachable, returns a non-200 status, or the result is absent.
        """
        url = f"{self._base_url}/v1/data/{package}"
        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)

        try:
            async with self._session.post(
                url,
                json={"input": input_data},
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    logger.warning("OPA returned HTTP %d for package %r", resp.status, package)
                    return {}

                body: dict[str, Any] = await resp.json(content_type=None)
                return body.get("result") or {}

        except Exception:
            logger.exception("OPA query failed (package=%r, url=%s)", package, url)
            return {}
