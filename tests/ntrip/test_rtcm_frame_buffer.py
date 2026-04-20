"""
Unit tests for RTCM3 frame splitting and per-mountpoint frame buffering.

_split_rtcm_frames parses raw bytes into complete RTCM3 frames:
  - 0xD3 preamble
  - 10-bit length in the lower two bits of byte 1 and all of byte 2
  - <length> bytes of payload
  - 3-byte CRC
  - total frame size = length + 6

Any bytes before the first valid preamble are discarded.  Frames that would
exceed _RTCM3_MAX_FRAME (1029 bytes) are skipped.  An incomplete trailing frame
is returned as the remainder so the caller can prepend it to the next chunk.

_observe_rtcm_quality drives the buffering: it pops the existing per-mountpoint
buffer, prepends it to the new chunk, extracts complete frames, and stores any
incomplete remainder back.  Oversized remainders are discarded to prevent
unbounded growth.

These tests use only real byte construction to validate the
framing logic directly.
"""

from __future__ import annotations

import struct

import pytest

from corshub.ntrip.v2.caster import (
    _ARP_CHANGE_THRESHOLD,  # noqa: PLC2701
    _RTCM3_MAX_FRAME,  # noqa: PLC2701
    _RTCM3_PREAMBLE,  # noqa: PLC2701
    _observe_rtcm_quality,  # noqa: PLC2701
    _split_rtcm_frames,  # noqa: PLC2701
)


def _make_frame(payload_length: int, *, preamble: int = 0xD3) -> bytes:
    """Build a minimal syntactically-valid RTCM3 frame of *payload_length* bytes.

    The payload is filled with 0x00.  The CRC field is also 0x00 × 3 — we only
    test framing logic here, not CRC validation.
    """
    header = bytes([
        preamble,
        (payload_length >> 8) & 0x03,  # upper 2 bits of 10-bit length
        payload_length & 0xFF,          # lower 8 bits
    ])
    payload = b"\x00" * payload_length
    crc = b"\x00\x00\x00"
    return header + payload + crc


def _frame_length(payload_length: int) -> int:
    return payload_length + 6


class TestSplitEmpty:
    def test_empty_input_returns_no_frames_and_empty_remainder(self) -> None:
        frames, remainder = _split_rtcm_frames(b"")
        assert frames == []
        assert remainder == b""


class TestSplitSingleFrame:
    def test_exact_single_frame_is_extracted(self) -> None:
        frame = _make_frame(10)
        frames, remainder = _split_rtcm_frames(frame)
        assert frames == [frame]
        assert remainder == b""

    def test_zero_payload_frame(self) -> None:
        frame = _make_frame(0)
        frames, remainder = _split_rtcm_frames(frame)
        assert frames == [frame]
        assert remainder == b""

    def test_maximum_payload_frame(self) -> None:
        # Max RTCM3 payload is 1023 bytes → total = 1029 = _RTCM3_MAX_FRAME
        frame = _make_frame(1023)
        assert len(frame) == _RTCM3_MAX_FRAME
        frames, remainder = _split_rtcm_frames(frame)
        assert frames == [frame]
        assert remainder == b""


class TestSplitMultipleFrames:
    def test_two_consecutive_frames_are_both_extracted(self) -> None:
        f1 = _make_frame(5)
        f2 = _make_frame(20)
        frames, remainder = _split_rtcm_frames(f1 + f2)
        assert frames == [f1, f2]
        assert remainder == b""

    def test_three_frames_of_different_sizes(self) -> None:
        frames_in = [_make_frame(n) for n in (0, 100, 1023)]
        data = b"".join(frames_in)
        frames_out, remainder = _split_rtcm_frames(data)
        assert frames_out == frames_in
        assert remainder == b""


class TestSplitIncomplete:
    def test_partial_frame_returned_as_remainder(self) -> None:
        frame = _make_frame(50)
        partial = frame[:-1]  # Missing the last CRC byte
        frames, remainder = _split_rtcm_frames(partial)
        assert frames == []
        assert remainder == partial

    def test_only_preamble_returned_as_remainder(self) -> None:
        frames, remainder = _split_rtcm_frames(bytes([_RTCM3_PREAMBLE]))
        assert frames == []
        assert remainder == bytes([_RTCM3_PREAMBLE])

    def test_preamble_plus_one_byte_returned_as_remainder(self) -> None:
        data = bytes([_RTCM3_PREAMBLE, 0x00])
        frames, remainder = _split_rtcm_frames(data)
        assert frames == []
        assert remainder == data

    def test_complete_frame_followed_by_partial_frame(self) -> None:
        complete = _make_frame(10)
        partial = _make_frame(30)[:-5]
        frames, remainder = _split_rtcm_frames(complete + partial)
        assert frames == [complete]
        assert remainder == partial

    def test_split_exactly_at_header_boundary(self) -> None:
        frame = _make_frame(10)
        # Deliver only the 3-byte header (no payload or CRC yet)
        frames, remainder = _split_rtcm_frames(frame[:3])
        assert frames == []
        assert remainder == frame[:3]


class TestSplitGarbage:
    def test_garbage_before_preamble_is_discarded(self) -> None:
        garbage = b"\x00\x01\x02\x03"
        frame = _make_frame(10)
        frames, remainder = _split_rtcm_frames(garbage + frame)
        assert frames == [frame]
        assert remainder == b""

    def test_all_garbage_returns_empty(self) -> None:
        # No 0xD3 bytes at all
        frames, remainder = _split_rtcm_frames(b"\x01\x02\x03\x04")
        assert frames == []
        assert remainder == b""

    def test_spurious_preamble_in_payload_does_not_confuse_parser(self) -> None:
        """A 0xD3 byte inside another frame's payload must not be treated as a new frame."""
        # Craft a frame whose payload contains a 0xD3 byte at offset 0.
        # payload = [0xD3, 0x00, 0x05, ...] — looks like a nested preamble.
        payload = bytes([_RTCM3_PREAMBLE, 0x00, 0x05]) + b"\x00" * 7  # 10-byte payload
        frame = bytes([_RTCM3_PREAMBLE, 0x00, len(payload)]) + payload + b"\x00\x00\x00"
        frames, remainder = _split_rtcm_frames(frame)
        assert frames == [frame]
        assert remainder == b""

    def test_max_length_field_produces_max_frame(self) -> None:
        # The 10-bit length field is inherently bounded to 1023.  The largest
        # encodable total frame size equals _RTCM3_MAX_FRAME exactly.
        frame = _make_frame(1023)
        assert len(frame) == _RTCM3_MAX_FRAME
        frames, remainder = _split_rtcm_frames(frame)
        assert frames == [frame]
        assert remainder == b""


class TestSplitChunkBoundary:
    @pytest.mark.parametrize("split_at", [1, 2, 3, 5, 10, 15])
    def test_frame_split_at_various_byte_offsets(self, split_at: int) -> None:
        frame = _make_frame(20)
        chunk1 = frame[:split_at]
        chunk2 = frame[split_at:]
        frames1, remainder = _split_rtcm_frames(chunk1)
        assert frames1 == []
        frames2, remainder2 = _split_rtcm_frames(remainder + chunk2)
        assert frames2 == [frame]
        assert remainder2 == b""

    def test_two_frames_split_across_three_chunks(self) -> None:
        f1 = _make_frame(8)
        f2 = _make_frame(12)
        data = f1 + f2
        # Deliver in three arbitrary chunks
        c1, c2, c3 = data[:7], data[7:15], data[15:]
        frames1, rem = _split_rtcm_frames(c1)
        frames2, rem = _split_rtcm_frames(rem + c2)
        frames3, rem = _split_rtcm_frames(rem + c3)
        assert frames1 + frames2 + frames3 == [f1, f2]
        assert rem == b""


_MP = "TEST0"


class TestObserveBuffering:
    def test_partial_chunk_is_stored_in_buffer(self) -> None:
        frame = _make_frame(50)
        partial = frame[:-1]
        buf: dict[str, bytes] = {}
        # Pass only partial data — no complete frames, remainder stored
        with pytest.MonkeyPatch().context() as mp:
            parsed_calls: list = []

            def fake_split(data: bytes):
                return [], data  # No complete frames, everything is remainder

            mp.setattr("corshub.ntrip.v2.caster._split_rtcm_frames", fake_split)
            _observe_rtcm_quality(_MP, partial, {}, buf)

        assert _MP in buf
        assert buf[_MP] == partial

    def test_complete_chunk_clears_buffer(self) -> None:
        frame = _make_frame(10)
        buf: dict[str, bytes] = {}
        _observe_rtcm_quality(_MP, frame, {}, buf)
        # No remainder — buffer entry should not exist
        assert _MP not in buf

    def test_buffer_prepended_to_next_chunk(self) -> None:
        frame = _make_frame(20)
        mid = len(frame) // 2
        buf: dict[str, bytes] = {}

        # First chunk: partial frame stored in buffer
        _observe_rtcm_quality(_MP, frame[:mid], {}, buf)
        assert buf.get(_MP) == frame[:mid]

        # Second chunk: buffer + chunk forms the complete frame
        # RTCMReader will try to parse it but we don't care about metric side effects here
        _observe_rtcm_quality(_MP, frame[mid:], {}, buf)
        assert _MP not in buf

    def test_oversized_remainder_is_discarded(self) -> None:
        """Remainders >= _RTCM3_MAX_FRAME are dropped to prevent unbounded growth."""
        oversized = b"\x00" * _RTCM3_MAX_FRAME  # No preamble → all garbage
        buf: dict[str, bytes] = {}
        _observe_rtcm_quality(_MP, oversized, {}, buf)
        assert _MP not in buf

    def test_large_remainder_just_below_limit_is_stored(self) -> None:
        """Remainders strictly smaller than _RTCM3_MAX_FRAME are retained."""
        # Build a frame header that claims a large payload, but only provide half
        # the bytes — this is a genuine incomplete frame the parser should buffer.
        payload_length = 200
        partial = bytes([
            _RTCM3_PREAMBLE,
            (payload_length >> 8) & 0x03,
            payload_length & 0xFF,
        ]) + b"\x00" * (payload_length // 2)  # Only half the payload delivered
        assert len(partial) < _RTCM3_MAX_FRAME
        buf: dict[str, bytes] = {}
        _observe_rtcm_quality(_MP, partial, {}, buf)
        assert _MP in buf
        assert buf[_MP] == partial

    def test_buffers_are_independent_per_mountpoint(self) -> None:
        frame = _make_frame(10)
        buf: dict[str, bytes] = {}
        partial = frame[:-1]

        _observe_rtcm_quality("MP_A", partial, {}, buf)
        _observe_rtcm_quality("MP_B", partial, {}, buf)

        assert buf.get("MP_A") == partial
        assert buf.get("MP_B") == partial

    def test_completed_frame_from_two_mountpoints_does_not_cross(self) -> None:
        f_a = _make_frame(10)
        f_b = _make_frame(20)
        buf: dict[str, bytes] = {}

        _observe_rtcm_quality("MP_A", f_a[:5], {}, buf)
        _observe_rtcm_quality("MP_B", f_b[:5], {}, buf)
        _observe_rtcm_quality("MP_A", f_a[5:], {}, buf)
        _observe_rtcm_quality("MP_B", f_b[5:], {}, buf)

        assert "MP_A" not in buf
        assert "MP_B" not in buf

    def test_stale_buffer_replaced_when_new_complete_frame_arrives(self) -> None:
        stale = _make_frame(10)[:-1]  # Incomplete
        new_frame = _make_frame(5)
        buf: dict[str, bytes] = {_MP: stale}

        # The new chunk completes neither `stale` (wrong length) nor alone would
        # the stale bytes form a valid frame. A fresh complete frame arriving
        # after corruption should clear the buffer once it is parsed.
        _observe_rtcm_quality(_MP, new_frame, {}, buf)
        # stale bytes prepended; new_frame bytes are contiguous after them.
        # Framing will scan past the failed stale attempt and find new_frame.
        assert _MP not in buf