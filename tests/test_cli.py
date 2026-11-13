"""The production entrypoint's flags must reach the app config.

The parser once used ``parse_known_args``, so the entrypoint's misspelled proxy
flags were silently dropped and every client behind the ingress shared one
auth rate-limit bucket.  These tests run the real module in a subprocess
because it parses ``sys.argv`` at import time.
"""

from __future__ import annotations

import re
import subprocess
import sys

from pathlib import Path


_ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh"
_PRINT_CONFIG = (
    "from corshub.bin.standalone import app, arguments;"
    "print(app.config.PROXIES_COUNT, app.config.REAL_IP_HEADER, arguments.access_logs)"
)


def _run(*flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _PRINT_CONFIG, *flags],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_entrypoint_flags_configure_reverse_proxy() -> None:
    flags = re.findall(r"--[a-z-]+(?:=\S+)?", _ENTRYPOINT.read_text())
    result = _run(*flags)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1].split() == ["1", "X-Real-IP", "True"]


def test_unknown_flag_is_rejected() -> None:
    result = _run("--proxies-count=1")
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
