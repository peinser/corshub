r"""
Generic constants that are utilized throughout the platform.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from corshub import env


if TYPE_CHECKING:
    from typing import Final


BASE: Final[str] = env.extract("BASE", optional=False, dtype=str)
