"""The synthetic debug mountpoint: valid RTCM, but never usable for navigation."""

from __future__ import annotations

import asyncio

from io import BytesIO

import pytest

from pyrtcm import RTCMReader

from corshub.ntrip.v2 import synthetic
from corshub.ntrip.v2.caster import NTRIPCaster
from corshub.ntrip.v2.caster import _split_rtcm_frames


def _decode(frame: bytes):  # type: ignore[no-untyped-def]
    _, msg = RTCMReader(BytesIO(frame), quitonerror=2, parsed=True).read()
    return msg


class TestRtcm1005:
    def test_decodes_with_an_independent_parser(self) -> None:
        msg = _decode(synthetic.rtcm_1005(6378137.0, 0.0, 0.0, station_id=4095))
        assert msg.identity == "1005"
        assert msg.DF003 == 4095
        assert msg.DF141 == 1  # flags a non-physical reference station
        position = (msg.DF025, msg.DF026, msg.DF027)
        assert position == pytest.approx((6378137.0, 0.0, 0.0))

    def test_negative_coordinates_round_trip(self) -> None:
        msg = _decode(synthetic.rtcm_1005(-1234567.8901, 2345678.1234, -3456789.0001, station_id=1))
        position = (msg.DF025, msg.DF026, msg.DF027)
        assert position == pytest.approx((-1234567.8901, 2345678.1234, -3456789.0001), abs=1e-4)

    def test_is_one_complete_frame_for_the_caster(self) -> None:
        frame = synthetic.rtcm_1005(6378137.0, 0.0, 0.0, station_id=4095)
        assert _split_rtcm_frames(frame) == ([frame], b"")


class TestRun:
    async def test_streams_but_is_never_routed_automatically(self) -> None:
        caster = NTRIPCaster()
        task = asyncio.create_task(synthetic.run(caster, interval=0.01))
        await asyncio.sleep(0)  # let it register

        mp = caster.mountpoints[synthetic.NAME]
        assert mp.verified is False
        assert "not for navigation" in (mp.identifier or "")
        assert caster.nearest(0.0, 0.0) is None  # right on top of it, still not chosen

        async with caster.subscribe(synthetic.NAME) as sub:
            frame = await sub.get(timeout=1.0)
        assert frame is not None
        assert _decode(frame).identity == "1005"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await caster.available(synthetic.NAME) is False
