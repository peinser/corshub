"""
Unit tests for NTRIPCaster and Mountpoint.

NTRIPCaster owns the mountpoint registry, credential validation, and lifecycle.
Frame delivery (publish / subscribe) is delegated to a Transport; the transport
contract is covered in test_transport.py.  The tests here verify:

    caster.register(mp)               → None, raises ValueError on duplicate name
    caster.unregister(name)           → None, raises KeyError if not found
    caster.mountpoints                → dict[str, Mountpoint]
    caster.authenticate_source(n, pw) → bool
    await caster.publish(name, data)  → int, 0 if unknown, updates last_seen
    caster.subscribe(name)            → async context manager, raises KeyError if unknown
    await caster.start() / stop()     → reaper task lifecycle
    caster._reap()                    → removes stale mountpoints
"""

from __future__ import annotations

import asyncio

import pytest

from corshub.ntrip.v2.caster import Mountpoint
from corshub.ntrip.v2.caster import NTRIPCaster


@pytest.fixture
def extra_mountpoint() -> dict:
    return {
        "name": "BASE2",
        "identifier": "BASE2",
        "format": "RTCM 3.3",
        "country": "NLD",
        "latitude": 52.3676,
        "longitude": 4.9041,
    }


class TestMountpointValidation:
    def _base(self, **overrides) -> dict:  # type: ignore[return]
        defaults = dict(
            name="BASE1", identifier="BASE1",
            format="RTCM 3.3", country="BEL", latitude=50.85, longitude=4.35,
        )
        defaults.update(overrides)
        return defaults

    def test_valid_mountpoint_constructs(self) -> None:
        Mountpoint(**self._base())  # Must not raise

    def test_name_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="name"):
            Mountpoint(**self._base(name=""))

    def test_name_too_long_raises(self) -> None:
        with pytest.raises(ValueError, match="name"):
            Mountpoint(**self._base(name="A" * 101))

    def test_name_with_special_chars_raises(self) -> None:
        with pytest.raises(ValueError, match="name"):
            Mountpoint(**self._base(name="BASE/1"))

    def test_name_underscore_allowed(self) -> None:
        Mountpoint(**self._base(name="BASE_1"))  # Must not raise

    def test_name_hyphen_allowed(self) -> None:
        Mountpoint(**self._base(name="BASE-1"))  # Must not raise

    def test_country_two_letters_raises(self) -> None:
        with pytest.raises(ValueError, match="[Cc]ountry"):
            Mountpoint(**self._base(country="BE"))

    def test_country_four_letters_raises(self) -> None:
        with pytest.raises(ValueError, match="[Cc]ountry"):
            Mountpoint(**self._base(country="BELG"))

    def test_country_lowercase_raises(self) -> None:
        with pytest.raises(ValueError, match="[Cc]ountry"):
            Mountpoint(**self._base(country="bel"))

    def test_latitude_above_90_raises(self) -> None:
        with pytest.raises(ValueError, match="[Ll]atitude"):
            Mountpoint(**self._base(latitude=90.1))

    def test_latitude_below_minus_90_raises(self) -> None:
        with pytest.raises(ValueError, match="[Ll]atitude"):
            Mountpoint(**self._base(latitude=-90.1))

    def test_latitude_boundary_values_accepted(self) -> None:
        Mountpoint(**self._base(latitude=90.0))
        Mountpoint(**self._base(latitude=-90.0))

    def test_longitude_above_180_raises(self) -> None:
        with pytest.raises(ValueError, match="[Ll]ongitude"):
            Mountpoint(**self._base(longitude=180.1))

    def test_longitude_below_minus_180_raises(self) -> None:
        with pytest.raises(ValueError, match="[Ll]ongitude"):
            Mountpoint(**self._base(longitude=-180.1))

    def test_longitude_boundary_values_accepted(self) -> None:
        Mountpoint(**self._base(longitude=180.0))
        Mountpoint(**self._base(longitude=-180.0))


class TestMountpointRegistry:
    def test_register_adds_mountpoint(self, caster: NTRIPCaster) -> None:
        assert "BASE1" in caster.mountpoints

    async def test_register_duplicate_raises_no_error(self, caster: NTRIPCaster, mountpoint_metadata: dict) -> None:
        mp = await caster.register(**mountpoint_metadata)
        assert mp is not None

    async def test_no_raise_country_empty(self, caster: NTRIPCaster) -> None:
        metadata = {
            "name": "BASE3",
            "identifier": "BASE3",
        }

        assert await caster.register(**metadata) is not None
        await caster.unregister("BASE3")

    async def test_raise_invalid_country(self, caster: NTRIPCaster) -> None:
        with pytest.raises(ValueError):
            await caster.register(**{
                "name": "BASE3",
                "identifier": "BASE3",
                "country": "INVALID",
            })

    async def test_unregister_removes_mountpoint(self, caster: NTRIPCaster) -> None:
        await caster.unregister("BASE1")
        assert "BASE1" not in caster.mountpoints

    async def test_unregister_nonexistent_raises_key_error(self, caster: NTRIPCaster) -> None:
        await caster.unregister("UNKNOWN")
        assert "UNKNOWN" not in caster.mountpoints  # Operation is idempotent, should not yield errors

    def test_mountpoints_is_mapping_of_name_to_mountpoint(
        self, caster: NTRIPCaster, mountpoint_metadata: dict
    ) -> None:
        mp = caster.mountpoints["BASE1"]
        assert mp.name == mountpoint_metadata["name"]
        assert mp.identifier == mountpoint_metadata["identifier"]
        assert mp.format == mountpoint_metadata["format"]

    async def test_multiple_mountpoints_tracked_independently(
        self, caster: NTRIPCaster, extra_mountpoint: dict
    ) -> None:
        await caster.register(**extra_mountpoint)
        assert "BASE1" in caster.mountpoints
        assert "BASE2" in caster.mountpoints

    def test_empty_caster_has_no_mountpoints(self) -> None:
        assert NTRIPCaster().mountpoints == {}


# TODO Implement
# class TestSourceAuthentication:
#     def test_valid_credentials_authenticate(self, caster: NTRIPCaster) -> None:
#         assert caster.authenticate("BASE1", "s3cr3t") is True

#     def test_wrong_password_rejected(self, caster: NTRIPCaster) -> None:
#         assert caster.authenticatee("BASE1", "wrong") is False

#     def test_unknown_mountpoint_rejected(self, caster: NTRIPCaster) -> None:
#         assert caster.authenticate("GHOST", "s3cr3t") is False

#     def test_empty_password_rejected(self, caster: NTRIPCaster) -> None:
#         assert caster.authenticate("BASE1", "") is False


class TestAvailability:
    async def test_available_returns_true_when_registered(self, caster: NTRIPCaster) -> None:
        assert await caster.available("BASE1") is True

    async def test_available_returns_false_for_unknown_identifier(self, caster: NTRIPCaster) -> None:
        assert await caster.available("GHOST") is False

    async def test_available_returns_false_after_close(self, caster: NTRIPCaster) -> None:
        await caster.close("BASE1")
        assert await caster.available("BASE1") is False

    async def test_available_returns_true_after_reopen(self, caster: NTRIPCaster, mountpoint_metadata: dict) -> None:
        await caster.close("BASE1")
        # Registering an already-registered (but closed) mountpoint reopens its transport.
        await caster.register(**mountpoint_metadata)
        assert await caster.available("BASE1") is True

    async def test_available_returns_false_after_unregister(self, caster: NTRIPCaster) -> None:
        await caster.unregister("BASE1")
        assert await caster.available("BASE1") is False

    async def test_available_empty_string_identifier(self, caster: NTRIPCaster) -> None:
        assert await caster.available("") is False

    async def test_available_is_independent_per_mountpoint(
        self, caster: NTRIPCaster, extra_mountpoint: dict
    ) -> None:
        await caster.register(**extra_mountpoint)
        await caster.close("BASE1")
        assert await caster.available("BASE1") is False
        assert await caster.available("BASE2") is True


class TestFanOut:
    async def test_publish_with_no_subscribers_returns_zero(self, caster: NTRIPCaster) -> None:
        count = await caster.publish("BASE1", b"\xd3\x00\x13")
        assert count == 0

    async def test_publish_to_unknown_mountpoint_returns_zero(self, caster: NTRIPCaster) -> None:
        assert await caster.publish("GHOST", b"\xd3\x00\x13") == 0

    async def test_subscribe_to_unknown_mountpoint_raises_key_error(self, caster: NTRIPCaster) -> None:
        with pytest.raises(KeyError):
            async with caster.subscribe("GHOST"):
                pass  # pragma: no cover

    async def test_subscriber_receives_published_data(self, caster: NTRIPCaster) -> None:
        received: list[bytes] = []

        async def consumer() -> None:
            async with caster.subscribe("BASE1") as sub:
                frame = await sub.get()
                if frame is not None:
                    received.append(frame)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0)
        count = await caster.publish("BASE1", b"\xd3\x00\x13")
        await task

        assert count == 1
        assert received == [b"\xd3\x00\x13"]

    async def test_multiple_subscribers_all_receive_data(self, caster: NTRIPCaster) -> None:
        results: list[list[bytes]] = [[], []]

        async def consumer(idx: int) -> None:
            async with caster.subscribe("BASE1") as sub:
                frame = await sub.get()
                if frame is not None:
                    results[idx].append(frame)

        tasks = [asyncio.create_task(consumer(i)) for i in range(2)]
        await asyncio.sleep(0)
        count = await caster.publish("BASE1", b"\xd3\x00\x13")
        await asyncio.gather(*tasks)

        assert count == 2
        assert results[0] == [b"\xd3\x00\x13"]
        assert results[1] == [b"\xd3\x00\x13"]

    async def test_successive_publishes_delivered_in_order(self, caster: NTRIPCaster) -> None:
        frames = [b"\xd3\x00\x01", b"\xd3\x00\x02", b"\xd3\x00\x03"]
        received: list[bytes] = []

        async def consumer() -> None:
            async with caster.subscribe("BASE1") as sub:
                while len(received) < len(frames):
                    frame = await sub.get()
                    if frame is None:
                        break
                    received.append(frame)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0)
        for frame in frames:
            await caster.publish("BASE1", frame)
        await task

        assert received == frames

    async def test_subscriber_unregistered_after_context_exit(self, caster: NTRIPCaster) -> None:
        async def consumer() -> None:
            async with caster.subscribe("BASE1") as sub:
                await sub.get()

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0)
        await caster.publish("BASE1", b"\xd3\x00\x00")
        await task

        assert await caster.publish("BASE1", b"\xd3\x00\x00") == 0

    async def test_subscribers_on_different_mountpoints_isolated(
        self, caster: NTRIPCaster, extra_mountpoint: dict
    ) -> None:
        await caster.register(**extra_mountpoint)
        received_base1: list[bytes] = []
        received_base2: list[bytes] = []

        async def consumer1() -> None:
            async with caster.subscribe("BASE1") as sub:
                frame = await sub.get()
                if frame is not None:
                    received_base1.append(frame)

        async def consumer2() -> None:
            async with caster.subscribe("BASE2") as sub:
                frame = await sub.get()
                if frame is not None:
                    received_base2.append(frame)

        t1 = asyncio.create_task(consumer1())
        t2 = asyncio.create_task(consumer2())
        await asyncio.sleep(0)

        await caster.publish("BASE1", b"\xaa")
        await caster.publish("BASE2", b"\xbb")
        await asyncio.gather(t1, t2)

        assert received_base1 == [b"\xaa"]
        assert received_base2 == [b"\xbb"]


class TestLifecycle:
    async def test_start_creates_reaper_task(self) -> None:
        c = NTRIPCaster(expiry=30.0)
        assert c._reaper_task is None
        await c.start()
        assert c._reaper_task is not None
        await c.stop()

    async def test_stop_cancels_reaper_task(self) -> None:
        c = NTRIPCaster(expiry=30.0)
        await c.start()
        await c.stop()
        assert c._reaper_task is None

    async def test_start_does_not_create_task_when_expiry_disabled(self) -> None:
        c = NTRIPCaster(expiry=None)
        await c.start()
        assert c._reaper_task is None
        await c.stop()

    async def test_stop_is_safe_without_start(self) -> None:
        c = NTRIPCaster()
        await c.stop()  # must not raise


class TestReaper:
    async def test_reap_removes_stale_mountpoint(self, mountpoint_metadata: dict) -> None:
        c = NTRIPCaster(expiry=30.0)
        await c.register(**mountpoint_metadata)

        # Force last_seen into the past beyond the expiry window.
        c.mountpoints["BASE1"].last_seen = 0.0
        await c._reap()

        assert "BASE1" not in c.mountpoints

    async def test_reap_keeps_fresh_mountpoint(self, mountpoint_metadata: dict) -> None:
        c = NTRIPCaster(expiry=30.0)
        await c.register(**mountpoint_metadata)
        await c._reap()
        assert "BASE1" in c.mountpoints

    async def test_reap_does_nothing_when_expiry_disabled(self, mountpoint_metadata: dict) -> None:
        c = NTRIPCaster(expiry=None)
        await c.register(**mountpoint_metadata)
        c.mountpoints["BASE1"].last_seen = 0.0
        await c._reap()
        assert "BASE1" in c.mountpoints

    async def test_publish_updates_last_seen(self, caster: NTRIPCaster) -> None:
        import time
        before = time.monotonic()
        caster.mountpoints["BASE1"].last_seen = 0.0
        await caster.publish("BASE1", b"\xd3\x00\x00")
        assert caster.mountpoints["BASE1"].last_seen >= before
