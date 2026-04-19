"""
Unit tests for base station ARP position change detection.

_observe_rtcm_quality tracks the Antenna Reference Point (ARP) reported in
RTCM 1005/1006 messages. The first position seen for a mountpoint is stored as
the reference; any subsequent position that deviates by more than
_ARP_CHANGE_THRESHOLD metres increments the ARP-changes counter and updates the
reference to the new position.

Tests use unittest.mock to patch RTCMReader so that no valid RTCM binary needs
to be constructed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import corshub.metrics as metrics
from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.ntrip.v2.caster import _observe_rtcm_quality
from corshub.ntrip.v2.caster import _ARP_CHANGE_THRESHOLD


# Brussels ECEF coordinates (approximate), in metres.
_X = 3975478.0
_Y =  302283.0
_Z = 4986670.0

_MOUNTPOINT = "BASE1"


def _arp_msg(x: float, y: float, z: float, msg_type: int = 1005) -> MagicMock:
    msg = MagicMock()
    msg.identity = str(msg_type)
    msg.DF025 = x
    msg.DF026 = y
    msg.DF027 = z
    return msg


def _run_observe(
    arp_reference: dict,
    *messages: MagicMock,
) -> None:
    """Call _observe_rtcm_quality with a sequence of mock parsed messages."""
    with patch("corshub.ntrip.v2.caster.RTCMReader") as MockReader:
        instance = MockReader.return_value
        instance.read.side_effect = [(b"raw", m) for m in messages] + [(None, None)]
        _observe_rtcm_quality(_MOUNTPOINT, b"dummy", arp_reference)


def _arp_changes(mountpoint: str = _MOUNTPOINT) -> float:
    return metrics.base_station_arp_changes_total.labels(mountpoint=mountpoint)._value.get()


def _arp_gauge(axis: str, mountpoint: str = _MOUNTPOINT) -> float:
    return metrics.base_station_arp_ecef_meters.labels(mountpoint=mountpoint, axis=axis)._value.get()


class TestARPReferenceInitialisation:
    def test_first_message_sets_reference(self) -> None:
        ref: dict = {}
        _run_observe(ref, _arp_msg(_X, _Y, _Z))
        assert ref[_MOUNTPOINT] == (_X, _Y, _Z)

    def test_first_message_sets_gauge(self) -> None:
        ref: dict = {}
        _run_observe(ref, _arp_msg(_X, _Y, _Z))
        assert _arp_gauge("x") == _X
        assert _arp_gauge("y") == _Y
        assert _arp_gauge("z") == _Z

    def test_message_1006_also_sets_reference(self) -> None:
        ref: dict = {}
        _run_observe(ref, _arp_msg(_X, _Y, _Z, msg_type=1006))
        assert ref[_MOUNTPOINT] == (_X, _Y, _Z)


class TestARPChangeDetection:
    def test_identical_position_does_not_increment_counter(self) -> None:
        ref: dict = {}
        before = _arp_changes()
        _run_observe(ref, _arp_msg(_X, _Y, _Z))
        _run_observe(ref, _arp_msg(_X, _Y, _Z))
        assert _arp_changes() == before

    def test_position_change_above_threshold_increments_counter(self) -> None:
        ref: dict = {}
        before = _arp_changes()
        _run_observe(ref, _arp_msg(_X, _Y, _Z))
        _run_observe(ref, _arp_msg(_X + _ARP_CHANGE_THRESHOLD + 0.001, _Y, _Z))
        assert _arp_changes() == before + 1

    def test_position_change_below_threshold_does_not_increment_counter(self) -> None:
        ref: dict = {}
        before = _arp_changes()
        _run_observe(ref, _arp_msg(_X, _Y, _Z))
        _run_observe(ref, _arp_msg(_X + _ARP_CHANGE_THRESHOLD * 0.5, _Y, _Z))
        assert _arp_changes() == before

    def test_position_change_updates_reference(self) -> None:
        ref: dict = {}
        new_x = _X + 1.0
        _run_observe(ref, _arp_msg(_X, _Y, _Z))
        _run_observe(ref, _arp_msg(new_x, _Y, _Z))
        assert ref[_MOUNTPOINT] == (new_x, _Y, _Z)

    def test_each_subsequent_change_increments_counter(self) -> None:
        ref: dict = {}
        before = _arp_changes()
        _run_observe(ref, _arp_msg(_X, _Y, _Z))
        _run_observe(ref, _arp_msg(_X + 1.0, _Y, _Z))
        _run_observe(ref, _arp_msg(_X + 2.0, _Y, _Z))
        assert _arp_changes() == before + 2

    @pytest.mark.parametrize("axis,dx,dy,dz", [
        ("x", 1.0,  0.0,  0.0),
        ("y", 0.0,  1.0,  0.0),
        ("z", 0.0,  0.0,  1.0),
    ])
    def test_change_detected_on_each_axis(
        self, axis: str, dx: float, dy: float, dz: float
    ) -> None:
        ref: dict = {}
        before = _arp_changes()
        _run_observe(ref, _arp_msg(_X, _Y, _Z))
        _run_observe(ref, _arp_msg(_X + dx, _Y + dy, _Z + dz))
        assert _arp_changes() == before + 1


class TestARPCasterIsolation:
    def test_two_casters_have_independent_references(self) -> None:
        c1 = NTRIPCaster()
        c2 = NTRIPCaster()
        _run_observe(c1._arp_reference, _arp_msg(_X, _Y, _Z))
        assert _MOUNTPOINT in c1._arp_reference
        assert _MOUNTPOINT not in c2._arp_reference

    def test_change_on_one_caster_does_not_affect_other(self) -> None:
        c1 = NTRIPCaster()
        c2 = NTRIPCaster()
        _run_observe(c1._arp_reference, _arp_msg(_X, _Y, _Z))
        _run_observe(c2._arp_reference, _arp_msg(_X + 50.0, _Y, _Z))
        # c2 sees a different first position, no change registered yet
        assert c2._arp_reference[_MOUNTPOINT] == (_X + 50.0, _Y, _Z)
        assert c1._arp_reference[_MOUNTPOINT] == (_X, _Y, _Z)


class TestARPMissingAttributes:
    def test_message_without_df025_is_skipped_gracefully(self) -> None:
        ref: dict = {}
        msg = MagicMock(spec=[])  # No attributes at all
        msg.identity = "1005"
        _run_observe(ref, msg)
        assert _MOUNTPOINT not in ref  # Reference not set, no crash
