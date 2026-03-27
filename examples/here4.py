r"""
A prototype to dissipate RTK corrections from a Here4 Base Station to a NTRIP caster.

High-level flow
---------------
1. **Device discovery** — Serial ports are scanned for u-blox hardware (USB vendor ID 0x1546
   or device name heuristics). The first matching port is opened at 115 200 baud.

2. **Initial configuration** — Three periodic UBX messages are enabled on the port:
   NAV-PVT (position / velocity / time at 1 Hz), NAV-SAT (per-satellite signal data at 1 Hz),
   and NAV-SVIN (survey-in progress at 1 Hz).

3. **MONITORING** — The live display shows position, velocity, pDOP, UTC time, and a satellite
   C/N0 table.  The software waits until the receiver reports a valid 3-D GNSS fix
   (fixType ≥ 3 and gnssFixOk).

4. **SURVEY_IN** — On the first valid fix, two things are triggered via serial commands:
   a. CFG-TMODE3 with rcvrMode=1 starts survey-in: the receiver accumulates observations and
      continuously refines its mean position estimate until both the minimum duration
      (SVIN_MIN_DUR seconds) and the accuracy limit (SVIN_ACC_LIMIT × 0.1 mm) are met.
   b. CFG-MSG commands enable the standard RTCM 3.3 correction messages on the port
      (1005 reference station ARP, MSM4/MSM7 for GPS / GLONASS / Galileo / BeiDou, 1230
      GLONASS code-phase biases) so they are ready to stream once the fix is established.

   Note on accuracy: SVIN_ACC_LIMIT governs the base station's own position accuracy, not
   the rover's.  RTK corrections always achieve centimetre-level *relative* accuracy
   (rover − base vector).  The rover's *absolute* accuracy is base error + ~1–2 cm.
   A 2 m limit is a practical compromise: survey-in finishes in minutes and gives
   sub-3 m absolute rover accuracy.  For true sub-metre absolute accuracy, place the
   base on a known surveyed mark and use CFG-TMODE3 fixed mode with precise coordinates.

5. **FIXED** — Once the receiver reports survey-in valid and no longer active, an output file
   is opened and every raw RTCM correction frame received from the serial port is appended to
   it verbatim.  This file can be tailed and forwarded directly to an NTRIP caster.

Use asyncio as much as possible (including serial read from USB).

Metrics glossary
----------------
H-Acc (horizontal accuracy estimate)
    1-sigma (68 % confidence) radius of the horizontal position error, in metres.
    Computed by the receiver's Kalman filter from the pseudorange residuals and satellite
    geometry.  Typical standalone GNSS: 1–5 m.  RTK fixed: 0.01–0.02 m.

V-Acc (vertical accuracy estimate)
    Same concept as H-Acc but for the vertical (altitude) axis.  Vertical is inherently
    weaker than horizontal because all satellites are above the horizon, so V-Acc is
    typically 1.5–2× H-Acc.

pDOP (Position Dilution of Precision)
    A dimensionless multiplier that captures how satellite geometry amplifies ranging
    errors into position error: position_error ≈ pDOP × pseudorange_noise.
    Determined purely by the angles between tracked satellites — more spread across the
    sky gives a lower (better) pDOP.  Excellent < 1.5, good < 2.5, poor > 4.

C/N0 (carrier-to-noise density ratio, dBHz)
    Signal strength of each tracked satellite.  Higher is better.
    < 20 dBHz: too weak to track reliably (shown red).
    20–34 dBHz: marginal — contributes to the fix but with high noise (yellow).
    ≥ 35 dBHz: healthy signal — carrier-phase tracking is stable (green).
    Typical open-sky values: 40–50 dBHz.  Obstructions, foliage, and multipath lower it.

Fix type
    0 No fix — insufficient satellites or geometry.
    2 2D fix — altitude is assumed/borrowed; only x/y are solved.
    3 3D fix — full position solution; minimum needed before survey-in is triggered.
    4 GNSS + dead reckoning — position is blended with IMU data (not applicable here).

gnssFixOk
    Flag set by the receiver when it considers the fix reliable enough to use.
    A 3D fix without gnssFixOk (e.g. during startup or severe multipath) will not
    trigger survey-in.

Survey-in mean accuracy
    The 3-D standard deviation of the running mean position estimate, in metres.
    Decreases as more observations are averaged.  Survey-in completes when this value
    drops below SVIN_ACC_LIMIT and SVIN_MIN_DUR seconds have elapsed.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import shutil
from enum import Enum
from enum import auto
from pathlib import Path
from typing import IO

import serial
import serial.tools.list_ports
from pyrtcm import RTCMMessage
from pyubx2 import RTCM3_PROTOCOL
from pyubx2 import SET
from pyubx2 import UBX_PROTOCOL
from pyubx2 import UBXMessage
from pyubx2 import UBXReader
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# ── Configuration ─────────────────────────────────────────────────────────────

BAUD_RATE = 115200
UBLOX_VID = 0x1546  # u-blox AG USB vendor ID

# Survey-in parameters.
# SVIN_ACC_LIMIT controls the base station's *own* position accuracy, not rover accuracy.
# RTK rover-to-base relative accuracy is always centimetre-level once a fix is achieved.
# Absolute rover accuracy ≈ base position error + ~1–2 cm.
# 20 000 × 0.1 mm = 2 m: practical for field work, survey-in finishes in a few minutes.
# For sub-metre absolute accuracy use a known surveyed point in fixed mode instead.
SVIN_MIN_DUR = 60
SVIN_ACC_LIMIT = 20_000  # 0.1 mm units → 2 m

RTCM_OUTPUT = Path("rtcm_corrections.rtcm")

# UBX class / message IDs
NAV_CLASS = 0x01
NAV_PVT_ID = 0x07
NAV_SAT_ID = 0x35
NAV_SVIN_ID = 0x3B
RTCM_CLASS = 0xF5

# RTCM 3.3 message IDs to enable (1005, 1074, 1084, 1094, 1124, 1230)
RTCM_OUTPUT_IDS = [0x05, 0x4A, 0x54, 0x5E, 0x7C, 0xE6]

FIX_NAMES = {
    0: "No fix",
    1: "Dead reckoning",
    2: "2D",
    3: "3D",
    4: "GNSS + DR",
    5: "Time only",
}

GNSS_NAMES = {0: "GPS", 1: "SBAS", 2: "Galileo", 3: "BDS", 4: "IMES", 5: "QZSS", 6: "GLONASS"}


# ── State ─────────────────────────────────────────────────────────────────────


class State(Enum):
    SEARCHING = auto()   # scanning for u-blox USB device
    CONNECTING = auto()  # opening serial port, sending initial config
    MONITORING = auto()  # streaming NAV data, waiting for stable 3D fix
    SURVEY_IN = auto()   # survey-in running
    FIXED = auto()       # fixed position established, streaming RTCM


class GNSSState:
    def __init__(self) -> None:
        self.state = State.SEARCHING
        self.port: str = "N/A"
        # NAV-PVT
        self.lat = self.lon = self.alt = self.speed = 0.0
        self.h_acc = self.v_acc = self.pdop = 0.0
        self.fix_type = 0
        self.gnss_fix_ok = False
        self.num_sv = 0
        self.utc_time = "N/A"
        # NAV-SAT
        self.satellites: list[dict] = []
        # NAV-SVIN
        self.svin_active = False
        self.svin_valid = False
        self.svin_dur = 0
        self.svin_obs = 0
        self.svin_acc = 0.0
        # RTCM output counters
        self.rtcm_msgs = 0
        self.rtcm_bytes = 0
        # H-Acc history for sparkline (1 Hz NAV-PVT → 120 s of history)
        self.h_acc_history: deque[float] = deque(maxlen=120)


# ── UBX helpers ───────────────────────────────────────────────────────────────


def _cfg_msg(msg_class: int, msg_id: int, rate: int) -> bytes:
    """Build a CFG-MSG command to set the output rate of a UBX/RTCM message."""
    return UBXMessage(
        "CFG",
        "CFG-MSG",
        SET,
        msgClass=msg_class,
        msgID=msg_id,
        rateI2C=0,
        rateUART1=rate,
        rateUART2=0,
        rateUSB=rate,
        rateSPI=0,
    ).serialize()


def _cfg_tmode3_svin(min_dur: int, acc_limit: int) -> bytes:
    """Build a CFG-TMODE3 survey-in command."""
    return UBXMessage(
        "CFG", "CFG-TMODE3", SET,
        version=0,
        rcvrMode=1,  # survey-in
        svinMinDur=min_dur,
        svinAccLimit=acc_limit,
    ).serialize()


def _cfg_tmode3_fixed(lat: float, lon: float, alt: float, acc_mm: float) -> bytes:
    """Build a CFG-TMODE3 fixed-mode command using LLA coordinates.

    lat, lon: decimal degrees; alt: metres above ellipsoid; acc_mm: known accuracy in mm.
    The coordinate is split into a standard-precision integer (1e-7 deg / cm) and a
    high-precision remainder (1e-9 deg / 0.1 mm) as required by the UBX protocol.
    """
    lat_i = int(lat * 1e7)
    lat_hp = round((lat * 1e7 - lat_i) * 100)   # units: 1e-9 deg
    lon_i = int(lon * 1e7)
    lon_hp = round((lon * 1e7 - lon_i) * 100)   # units: 1e-9 deg
    alt_cm = int(alt * 100)
    alt_hp = round((alt * 100 - alt_cm) * 10)   # units: 0.1 mm
    return UBXMessage(
        "CFG", "CFG-TMODE3", SET,
        version=0,
        rcvrMode=2,             # fixed mode
        lla=1,                  # coordinates are LLA, not ECEF
        ecefXOrLat=lat_i,
        ecefYOrLon=lon_i,
        ecefZOrAlt=alt_cm,
        ecefXOrLatHP=lat_hp,
        ecefYOrLonHP=lon_hp,
        ecefZOrAltHP=alt_hp,
        fixedPosAcc=int(acc_mm * 10),  # 0.1 mm units
    ).serialize()


# ── Device detection ──────────────────────────────────────────────────────────


def find_ublox_ports() -> list[str]:
    """Return serial port names that look like u-blox devices."""
    ports = []

    for p in serial.tools.list_ports.comports():
        if (
            p.vid == UBLOX_VID
            or "u-blox" in (p.description or "").lower()
            or "u-blox" in (p.manufacturer or "").lower()
            or p.device.startswith("/dev/ttyACM")
        ):
            ports.append(p.device)

    return ports


# ── Display ───────────────────────────────────────────────────────────────────


_SPARK = "▁▂▃▄▅▆▇█"


def _sparkline(values: deque[float]) -> str:
    """Render a deque of floats as a Unicode block sparkline, capped to terminal width."""
    if len(values) < 2:
        return ""
    max_len = max(10, shutil.get_terminal_size().columns - 6)  # subtract panel borders/padding
    data = list(values)[-max_len:]
    lo, hi = min(data), max(data)
    span = hi - lo or 1.0
    return "".join(_SPARK[round((v - lo) / span * 7)] for v in data)


def _cno_bar(cno: int) -> Text:
    """Render a C/N0 value as a coloured block bar (scale 0–50 dBHz, 10 chars wide)."""
    filled = round(max(0, min(cno, 50)) / 50 * 10)
    color = "green" if cno >= 35 else ("yellow" if cno >= 20 else "red")
    return Text(f"{'█' * filled}{'░' * (10 - filled)} {cno:2d}", style=color)


def build_display(gs: GNSSState) -> Table:
    """Render the full terminal display from current GNSSState."""
    root = Table.grid(padding=(0, 1))
    root.add_column()

    state_color = {
        State.SEARCHING: "yellow",
        State.CONNECTING: "cyan",
        State.MONITORING: "blue",
        State.SURVEY_IN: "magenta",
        State.FIXED: "green",
    }[gs.state]
    banner = Text(f"  {gs.state.name}  {gs.port}  ", style=f"bold white on {state_color}")
    root.add_row(Panel(banner, title="[bold]Here4 RTK Base Station Prototype[/bold]"))

    # Position / velocity
    pvt = Table(title="Position & Velocity", show_header=False, expand=True)
    pvt.add_column("k", style="cyan", width=14)
    pvt.add_column("v")
    pvt.add_column("k", style="cyan", width=14)
    pvt.add_column("v")
    gnss_fix_ok = gs.gnss_fix_ok and gs.fix_type >= 3
    fix_style = "green" if gnss_fix_ok else ("yellow" if gs.fix_type > 0 else "red")
    pvt.add_row("UTC", gs.utc_time, "Fix", Text(FIX_NAMES.get(gs.fix_type, "?"), style=fix_style))
    pvt.add_row("Latitude", f"{gs.lat:.8f}°", "Longitude", f"{gs.lon:.8f}°")
    pvt.add_row("Alt (MSL)", f"{gs.alt:.3f} m", "Speed", f"{gs.speed:.3f} m/s")
    pvt.add_row("H-Acc", f"{gs.h_acc:.3f} m", "V-Acc", f"{gs.v_acc:.3f} m")
    pvt.add_row("pDOP", f"{gs.pdop:.2f}", "SVs used", str(gs.num_sv))
    root.add_row(pvt)

    # Survey-in progress
    if gs.state in (State.SURVEY_IN, State.FIXED):
        sv = Table(title="Survey-In", show_header=False, expand=True)
        sv.add_column("k", style="cyan", width=14)
        sv.add_column("v")
        sv.add_row("Valid", Text("YES", style="green") if gs.svin_valid else Text("NO", style="red"))
        sv.add_row("Duration", f"{gs.svin_dur} s  (min {SVIN_MIN_DUR} s)")
        sv.add_row("Mean Accuracy", f"{gs.svin_acc:.3f} m  (limit {SVIN_ACC_LIMIT / 10000:.1f} m)")
        sv.add_row("Observations", str(gs.svin_obs))
        root.add_row(sv)

    # RTCM output statistics
    if gs.state == State.FIXED:
        rt = Table(title="RTCM Corrections Output", show_header=False, expand=True)
        rt.add_column("k", style="cyan", width=14)
        rt.add_column("v", style="green")
        rt.add_row("Output file", str(RTCM_OUTPUT))
        rt.add_row("Messages", str(gs.rtcm_msgs))
        rt.add_row("Bytes written", str(gs.rtcm_bytes))
        root.add_row(rt)

    # H-Acc sparkline (shown once we have history)
    if len(gs.h_acc_history) >= 2:
        spark = _sparkline(gs.h_acc_history)
        lo, hi = min(gs.h_acc_history), max(gs.h_acc_history)
        root.add_row(Panel(
            f"[cyan]{spark}[/cyan]\n"
            f"[dim]min {lo:.2f} m  ·  max {hi:.2f} m  ·  now {gs.h_acc_history[-1]:.2f} m  "
            f"·  {len(gs.h_acc_history)} s of history[/dim]",
            title="H-Acc trend  [dim](↓ better)[/dim]",
            expand=True,
        ))

    # Satellite table (top 16 by C/N0)
    if gs.satellites:
        st = Table(title=f"Satellites ({len(gs.satellites)} tracked)", show_header=True, expand=True)
        st.add_column("SV", width=5)
        st.add_column("System", width=8)
        st.add_column("Elev°", justify="right", width=6)
        st.add_column("Azim°", justify="right", width=6)
        st.add_column("C/N0 (dBHz)", width=18)
        st.add_column("Used", justify="center", width=5)
        for s in sorted(gs.satellites, key=lambda x: x["cno"], reverse=True)[:16]:
            st.add_row(
                str(s["svId"]),
                GNSS_NAMES.get(s["gnssId"], "?"),
                str(s["elev"]),
                str(s["azim"]),
                _cno_bar(s["cno"]),
                Text("✓", style="green") if s["used"] else Text("·", style="dim"),
            )
        root.add_row(st)

    return root


# ── Main ──────────────────────────────────────────────────────────────────────


async def main(args: argparse.Namespace) -> None:
    gs = GNSSState()
    queue: asyncio.Queue[tuple[bytes | None, object]] = asyncio.Queue(maxsize=512)
    ser_ref: list[serial.Serial] = []  # mutable slot so process_loop can send CFG commands

    # ── connect_loop: find device, open port, configure, spawn reader ──────────

    async def connect_loop() -> None:
        while True:
            if gs.state != State.SEARCHING:
                await asyncio.sleep(1)
                continue

            ports = find_ublox_ports()
            if not ports:
                await asyncio.sleep(2)
                continue

            gs.state = State.CONNECTING
            port_name = ports[0]
            gs.port = port_name
            loop = asyncio.get_running_loop()

            try:
                ser = await loop.run_in_executor(
                    None, lambda: serial.Serial(port_name, BAUD_RATE, timeout=1)
                )
            except Exception:
                gs.state = State.SEARCHING
                await asyncio.sleep(2)
                continue

            ser_ref.clear()
            ser_ref.append(ser)
            ubr = UBXReader(ser, protfilter=UBX_PROTOCOL | RTCM3_PROTOCOL)

            # Enable periodic NAV messages
            for cmd in [
                _cfg_msg(NAV_CLASS, NAV_PVT_ID, 1),
                _cfg_msg(NAV_CLASS, NAV_SAT_ID, 1),
                _cfg_msg(NAV_CLASS, NAV_SVIN_ID, 1),
            ]:
                await loop.run_in_executor(None, ser.write, cmd)
                await asyncio.sleep(0.05)

            gs.state = State.MONITORING
            asyncio.create_task(read_loop(ser, ubr))

    # ── read_loop: blocking serial read in thread executor → queue ─────────────

    async def read_loop(ser: serial.Serial, ubr: UBXReader) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                raw, parsed = await loop.run_in_executor(None, ubr.read)
                if parsed is not None:
                    try:
                        queue.put_nowait((raw, parsed))
                    except asyncio.QueueFull:
                        pass  # drop oldest-unprocessed message under load
            except Exception:
                # Serial error — trigger reconnect
                if ser_ref and ser_ref[0] is ser:
                    ser_ref.clear()
                    gs.state = State.SEARCHING
                    gs.satellites = []
                break

    # ── process_loop: dispatch parsed messages, drive state machine ────────────

    async def process_loop() -> None:
        loop = asyncio.get_running_loop()
        rtcm_file: IO[bytes] | None = None

        while True:
            raw, parsed = await queue.get()
            identity = getattr(parsed, "identity", None)

            # ── NAV-PVT: position, velocity, time ─────────────────────────────
            if identity == "NAV-PVT":
                gs.fix_type = parsed.fixType
                gs.gnss_fix_ok = bool(parsed.gnssFixOk)
                gs.lat = parsed.lat          # pyubx2 applies 1e-7 scale → degrees
                gs.lon = parsed.lon          # pyubx2 applies 1e-7 scale → degrees
                gs.alt = parsed.hMSL / 1e3  # raw mm → m
                gs.speed = parsed.gSpeed / 1e3  # raw mm/s → m/s
                gs.h_acc = parsed.hAcc / 1e3    # raw mm → m
                gs.v_acc = parsed.vAcc / 1e3    # raw mm → m
                gs.pdop = parsed.pDOP        # pyubx2 applies 1e-2 scale → dimensionless
                gs.num_sv = parsed.numSV
                gs.h_acc_history.append(gs.h_acc)
                if parsed.validDate and parsed.validTime:
                    gs.utc_time = (
                        f"{parsed.year:04d}-{parsed.month:02d}-{parsed.day:02d} "
                        f"{parsed.hour:02d}:{parsed.min:02d}:{parsed.second:02d} UTC"
                    )
                # Once we have a gnssFixOK 3D fix, kick off survey-in
                if (
                    gs.state == State.MONITORING
                    and gs.fix_type >= 3
                    and gs.gnss_fix_ok
                    and ser_ref
                ):
                    ser = ser_ref[0]
                    # Enable RTCM correction output messages (same for both modes)
                    for rtcm_id in RTCM_OUTPUT_IDS:
                        await loop.run_in_executor(None, ser.write, _cfg_msg(RTCM_CLASS, rtcm_id, 1))
                        await asyncio.sleep(0.05)
                    if args.lat is not None:
                        # Fixed mode: apply known coordinates immediately, skip survey-in
                        await loop.run_in_executor(
                            None, ser.write,
                            _cfg_tmode3_fixed(args.lat, args.lon, args.alt, args.fixed_acc),
                        )
                        gs.state = State.FIXED
                        rtcm_file = RTCM_OUTPUT.open("wb")
                    else:
                        # Survey-in mode: let the receiver converge on its own position
                        await loop.run_in_executor(
                            None, ser.write, _cfg_tmode3_svin(SVIN_MIN_DUR, SVIN_ACC_LIMIT)
                        )
                        gs.state = State.SURVEY_IN

            # ── NAV-SVIN: survey-in progress ───────────────────────────────────
            elif identity == "NAV-SVIN":
                gs.svin_active = bool(parsed.active)
                gs.svin_valid = bool(parsed.valid)
                gs.svin_obs = parsed.obs
                gs.svin_acc = parsed.meanAcc / 10000.0  # 0.1 mm → m
                gs.svin_dur = parsed.dur
                # Survey-in complete: open RTCM output file and move to FIXED
                if gs.state == State.SURVEY_IN and gs.svin_valid and not gs.svin_active:
                    gs.state = State.FIXED
                    rtcm_file = RTCM_OUTPUT.open("wb")

            # ── NAV-SAT: satellite signal strengths ────────────────────────────
            elif identity == "NAV-SAT":
                sats = []
                for i in range(1, parsed.numSvs + 1):
                    sats.append({
                        "gnssId": getattr(parsed, f"gnssId_{i:02d}", 0),
                        "svId": getattr(parsed, f"svId_{i:02d}", 0),
                        "cno": getattr(parsed, f"cno_{i:02d}", 0),
                        "elev": getattr(parsed, f"elev_{i:02d}", 0),
                        "azim": getattr(parsed, f"azim_{i:02d}", 0),
                        "used": bool(getattr(parsed, f"svUsed_{i:02d}", 0)),
                    })
                gs.satellites = sats

            # ── RTCM: write raw correction bytes to file ───────────────────────
            elif gs.state == State.FIXED and isinstance(parsed, RTCMMessage) and raw and rtcm_file:
                await loop.run_in_executor(None, rtcm_file.write, raw)
                await loop.run_in_executor(None, rtcm_file.flush)
                gs.rtcm_bytes += len(raw)
                gs.rtcm_msgs += 1

    # ── display_loop: refresh Rich live display at 2 Hz ───────────────────────

    async def display_loop(live: Live) -> None:
        while True:
            live.update(build_display(gs))
            await asyncio.sleep(0.5)

    console = Console()
    with Live(build_display(gs), console=console, refresh_per_second=2, screen=True) as live:
        await asyncio.gather(
            display_loop(live),
            connect_loop(),
            process_loop(),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Here4 RTK base station — streams RTCM corrections to a file."
    )
    fixed = parser.add_argument_group(
        "fixed mode",
        "Supply all three to skip survey-in and use a known surveyed position instead. "
        "Gives sub-centimetre absolute accuracy when the coordinates are survey-grade.",
    )
    fixed.add_argument("--lat", type=float, metavar="DEG", help="Base latitude (decimal degrees)")
    fixed.add_argument("--lon", type=float, metavar="DEG", help="Base longitude (decimal degrees)")
    fixed.add_argument("--alt", type=float, metavar="M",   help="Base altitude above ellipsoid (metres)")
    fixed.add_argument(
        "--fixed-acc", type=float, default=10.0, metavar="MM",
        help="Known accuracy of the fixed position in mm (default: 10)",
    )
    _args = parser.parse_args()
    if any(x is not None for x in (_args.lat, _args.lon, _args.alt)):
        if not all(x is not None for x in (_args.lat, _args.lon, _args.alt)):
            parser.error("--lat, --lon and --alt must all be provided together")
    try:
        asyncio.run(main(_args))
    except KeyboardInterrupt:
        pass
