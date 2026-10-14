"""Exercise a real projector and write a Markdown report.

Runs in stages, from harmless to intrusive. Only the read-only stages run by default:

1. discovery   listen for SDAP advertisements, and find the projector when --ip is not given
2. read        power status, every helper getter, every setting in protocol.SETTINGS, raw error status
3. faults      wrong community and a closed port, to check the error types and timings
4. write       (--write) change each setting, read it back, restore the original value
5. ir          (--ir) open and close the on-screen menu with simulated remote keys
6. power       (--power) switch off and on again, logging every power state with timestamps
7. soak        (--soak SECONDS) two threads polling at once, to find connection limits

Writes and power changes list what they will change and ask before they run unless --yes is
given. Every change is announced as it happens ("Changing HDR from OFF to ON"), and every
setting is put back afterwards, with retries, also when a step fails or you press Ctrl+C.
Anything that could not be put back is listed at the top of the report and at the end of the
output, with the command that sets it back by hand. The report is printed and written to
test_reports/ in the repository (git-ignored); use --redact before posting it anywhere public.

Without --ip, the projector is found via SDAP (and the library's own discovery for a
Projector() created without an IP is tested as well).

Examples::

    poetry run python scripts/hardware_check.py
    poetry run python scripts/hardware_check.py --ip 10.0.0.5
    poetry run python scripts/hardware_check.py --ip 10.0.0.5 --write --ir
    poetry run python scripts/hardware_check.py --ip 10.0.0.5 --write --ir --power --soak 600 --redact

Exit status is 0 when nothing FAILed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import datetime
import ipaddress
import platform
import statistics
import sys
import threading
import time
import traceback
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from pysdcp_extended import (
    DEFAULT_TCP_PORT,
    DEFAULT_TCP_TIMEOUT,
    DEFAULT_UDP_PORT,
    DiscoveredProjector,
    Projector,
    SDCPCommandError,
    SDCPConnectionError,
    SDCPError,
    discover_projectors,
)
from pysdcp_extended.protocol import ACTIONS, COMMANDS, COMMANDS_IR, SETTINGS

PASS, WARN, FAIL, SKIP, INFO = "PASS", "WARN", "FAIL", "SKIP", "INFO"
ICONS = {PASS: "✅", WARN: "⚠️", FAIL: "❌", SKIP: "⏭️", INFO: "ℹ️"}  # noqa: RUF001
# Settings that move the lens; left alone unless --include-lens is given.
LENS_SETTINGS = {"PICTURE_POSITION"}
COMMUNITY_ERROR = 0x201
# Reports go to <repo>/test_reports/ (git-ignored) wherever the script is run from.
REPORTS_DIR = Path(__file__).resolve().parent.parent / "test_reports"


@dataclass
class Result:
    stage: str
    name: str
    status: str
    detail: str
    ms: float | None = None


@dataclass
class Change:
    """One change the script made to the projector, for the report's change log."""

    what: str
    old: str
    new: str
    outcome: str = "sent"


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    power_log: list[tuple[float, str]] = field(default_factory=list)
    model: str | None = None
    serial: int | None = None
    private: dict[str, str] = field(default_factory=dict)
    """Identifying text -> placeholder, for --redact."""
    changes: list[Change] = field(default_factory=list)
    pending: dict[str, str] = field(default_factory=dict)
    """What was changed -> its original value, until the original is confirmed back."""
    stage: str = "start"
    interrupted: bool = False

    def announce(self, what: str, old: str, new: str) -> Change:
        """Say out loud what is about to change, and log it."""
        print(f">>> Changing {what} from {old} to {new}", file=sys.stderr, flush=True)
        change = Change(what, old, new)
        self.changes.append(change)
        return change

    def add(self, stage: str, name: str, status: str, detail: str, ms: float | None = None) -> Result:
        result = Result(stage, name, status, detail, ms)
        self.results.append(result)
        timing = f" ({ms:.0f} ms)" if ms is not None else ""
        print(f"[{status}] {stage}: {name}: {detail}{timing}", file=sys.stderr, flush=True)
        return result

    @property
    def failed(self) -> bool:
        return any(r.status == FAIL for r in self.results)


def timed(func: Callable[[], Any]) -> tuple[Any, BaseException | None, float]:
    """Call ``func`` and return (value, exception, milliseconds)."""
    started = time.perf_counter()
    try:
        value = func()
    except Exception as err:  # the whole point is to record whatever was raised
        return None, err, (time.perf_counter() - started) * 1000
    return value, None, (time.perf_counter() - started) * 1000


def describe(err: BaseException) -> str:
    return f"{type(err).__name__}: {err}"


def confirm(args: argparse.Namespace, question: str) -> bool:
    if args.yes:
        return True
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def make_projector(args: argparse.Namespace, **overrides: Any) -> Projector:
    options: dict[str, Any] = {
        "ip": args.ip,
        "community": args.community,
        "tcp_port": args.port,
        "tcp_timeout": args.timeout,
    }
    options.update(overrides)
    return Projector(**options)


def slow(args: argparse.Namespace, ms: float) -> bool:
    """True if a call took longer than the timeout plus a margin (a hang the library did not catch)."""
    return ms > (float(args.timeout) + 1.0) * 1000


DISCOVERY_TIPS = [
    "The computer and the projector must be on the same network segment: SDAP is a UDP broadcast and does not "
    "cross routers, separate VLANs, or Wi-Fi client isolation.",
    "The computer's firewall must let Python receive UDP on port 53862 (macOS and Windows may ask the first time).",
    "Docker containers and WSL2 only see the broadcast with host networking.",
    "The projector advertises every 30 seconds; listen for at least 31 (--discovery-timeout).",
    "Some models can switch the advertisement off in their network settings.",
]


def stage_discovery(args: argparse.Namespace, report: Report) -> None:
    """Listen for SDAP; when no --ip was given, pick the projector to test from what was heard."""
    if args.skip_discovery:
        report.add("discovery", "SDAP", SKIP, "--skip-discovery given")
        return
    print(f"Listening for SDAP advertisements for {args.discovery_timeout:.0f} s ...", file=sys.stderr, flush=True)
    found, err, ms = timed(partial(discover_projectors, timeout=args.discovery_timeout, udp_port=args.udp_port))
    if err is not None:
        status = WARN if isinstance(err, SDCPConnectionError) and args.ip else FAIL
        report.add("discovery", "SDAP", status, describe(err), ms)
        if args.ip is None:
            raise SystemExit(finish(args, report))
        return
    if not found:
        report.add("discovery", "SDAP", FAIL if args.ip is None else WARN, "no advertisement heard", ms)
        report.notes.extend(DISCOVERY_TIPS)
        if args.ip is None:
            report.notes.append("No --ip was given and none was discovered, so nothing else was tried.")
            raise SystemExit(finish(args, report))
        return

    # Sorted so the numbers in the choice prompt do not depend on which announcement arrived first.
    found = sorted(found, key=lambda item: (ipaddress.ip_address(item.ip), item.serial_number))
    for number, item in enumerate(found, start=1):
        suffix = "" if len(found) == 1 else f"-{number}"
        report.private[item.ip] = f"<projector-ip{suffix}>"
        report.private[str(item.serial_number)] = f"<serial{suffix}>"
        if item.info.location:
            report.private[item.info.location] = f"<location{suffix}>"
        report.add(
            "discovery",
            f"advertisement from {item.ip}",
            PASS,
            f"model {item.product_name!r}, serial {item.serial_number}, power state {item.power_state}, "
            f"community {item.header.community!r}, location {item.info.location!r}",
            ms,
        )

    if args.ip is None:
        chosen = choose_projector(args, found)
        if chosen is None:
            report.add("discovery", "projector to test", FAIL, f"{len(found)} projectors heard; pass --ip to pick one")
            raise SystemExit(finish(args, report))
        args.ip = chosen.ip
        report.add("discovery", "projector to test", PASS, f"using {chosen.ip}, found via SDAP")
    else:
        chosen = next((item for item in found if item.ip == args.ip), None)
        if chosen is None:
            report.add("discovery", "SDAP", WARN, f"{args.ip} was not among the projectors heard")
            return

    report.model, report.serial = chosen.product_name, chosen.serial_number
    advertised = chosen.header.community
    if args.community is None:
        args.community = advertised or "SONY"
        report.add("discovery", "community", PASS, f"using {args.community!r} from the advertisement")
    elif advertised and advertised != args.community:
        report.add("discovery", "community", WARN, f"projector advertises {advertised!r} but --community is {args.community!r}")

    if args.discovered:
        stage_auto_discovery(args, report, chosen)


def choose_projector(args: argparse.Namespace, found: list[DiscoveredProjector]) -> DiscoveredProjector | None:
    if len(found) == 1:
        return found[0]
    if args.yes:
        return None
    for number, item in enumerate(found, start=1):
        print(f"  {number}. {item.ip}  {item.product_name}  serial {item.serial_number}", file=sys.stderr)
    try:
        answer = input(f"Which projector should be tested? [1-{len(found)}] ")
    except EOFError:
        return None
    if answer.strip().isdigit() and 1 <= int(answer) <= len(found):
        return found[int(answer) - 1]
    return None


def stage_auto_discovery(args: argparse.Namespace, report: Report, chosen: DiscoveredProjector) -> None:
    """Check the library's own path for a Projector created without an IP."""
    print("Testing Projector() without an IP; waits for the next advertisement ...", file=sys.stderr, flush=True)
    projector = Projector(
        udp_port=args.udp_port, tcp_port=args.port, tcp_timeout=args.timeout, udp_timeout=args.discovery_timeout
    )
    status, err, ms = timed(projector.get_power_status)
    if err is not None:
        report.add("discovery", "Projector() with no IP", FAIL, describe(err), ms)
    elif projector.ip != chosen.ip:
        report.add(
            "discovery", "Projector() with no IP", INFO, f"found {projector.ip} first (another projector); power {status}", ms
        )
    else:
        report.add("discovery", "Projector() with no IP", PASS, f"found {projector.ip} by itself and read power {status}", ms)


def stage_read(args: argparse.Namespace, report: Report, projector: Projector) -> dict[str, Any]:
    """Read everything; return {setting name: value} for the settings that answered."""
    status, err, ms = timed(projector.get_power_status)
    if err is not None:
        report.add("read", "get_power_status", FAIL, describe(err), ms)
        report.notes.append("The projector did not answer the first command, so nothing else was tried.")
        raise SystemExit(finish(args, report))
    report.add("read", "get_power_status", FAIL if slow(args, ms) else PASS, str(status), ms)

    helpers: list[tuple[str, Callable[[], Any]]] = [
        ("get_power", projector.get_power),
        ("get_input", projector.get_input),
        ("get_muting", projector.get_muting),
        ("get_lamp_timer", projector.get_lamp_timer),
        ("get_lamp_hours", projector.get_lamp_hours),
        ("get_calibration_preset", projector.get_calibration_preset),
        ("get_aspect_ratio", projector.get_aspect_ratio),
        ("get_picture_position", projector.get_picture_position),
        ("get_HDMI1_dynamic_range", projector.get_HDMI1_dynamic_range),
        ("get_HDMI2_dynamic_range", projector.get_HDMI2_dynamic_range),
    ]
    for name, func in helpers:
        value, err, ms = timed(func)
        if err is None:
            report.add("read", name, FAIL if slow(args, ms) else PASS, repr(value), ms)
        elif isinstance(err, SDCPCommandError):
            report.add("read", name, INFO, f"not available now: {err.error_message}", ms)
        else:
            report.add("read", name, FAIL, describe(err), ms)

    values: dict[str, Any] = {}
    for name in SETTINGS:
        value, err, ms = timed(partial(projector.get_setting, name))
        if err is None and isinstance(value, int):
            report.add("read", f"setting {name}", WARN, f"raw value {value} (0x{value:04x}) is not in the value table", ms)
        elif err is None:
            report.add("read", f"setting {name}", FAIL if slow(args, ms) else PASS, str(value), ms)
            values[name] = value
        elif isinstance(err, SDCPCommandError):
            report.add("read", f"setting {name}", INFO, f"not available now: {err.error_message}", ms)
        else:
            report.add("read", f"setting {name}", FAIL, describe(err), ms)

    raw, err, ms = timed(lambda: projector.send_command(ACTIONS["GET"], COMMANDS["GET_STATUS_ERROR"]))
    if err is None:
        number = raw if isinstance(raw, int) else 0
        report.add("read", "raw GET_STATUS_ERROR", INFO, f"{raw!r} (0x{number:04x}, bits {number:016b})", ms)
    else:
        report.add("read", "raw GET_STATUS_ERROR", INFO if isinstance(err, SDCPCommandError) else FAIL, describe(err), ms)
    return values


def stage_faults(args: argparse.Namespace, report: Report) -> None:
    wrong = make_projector(args, community="XXXX" if args.community != "XXXX" else "YYYY")
    _, err, ms = timed(wrong.get_power)
    if isinstance(err, SDCPCommandError) and err.error_code == COMMUNITY_ERROR:
        report.add("faults", "wrong community", PASS, err.error_message, ms)
    elif err is None:
        report.add("faults", "wrong community", WARN, "projector answered anyway; it may not check the community", ms)
    else:
        report.add("faults", "wrong community", WARN, f"expected a Different Community error, got {describe(err)}", ms)

    closed = make_projector(args, tcp_port=args.port + 1)
    _, err, ms = timed(closed.get_power)
    if isinstance(err, SDCPConnectionError) and not slow(args, ms):
        report.add("faults", f"closed port {args.port + 1}", PASS, describe(err), ms)
    else:
        detail = describe(err) if err is not None else "projector answered on an unexpected port"
        report.add("faults", f"closed port {args.port + 1}", FAIL, detail, ms)


def stage_write(args: argparse.Namespace, report: Report, projector: Projector, values: dict[str, Any]) -> None:
    if not args.write:
        report.add("write", "settings", SKIP, "pass --write to change settings (each one is restored)")
        return
    if projector.get_power_status() != "POWER_ON":
        report.add("write", "settings", SKIP, "projector is not POWER_ON; turn it on, wait for a picture, and run again")
        return
    planned = {name: original for name, original in values.items() if name not in LENS_SETTINGS or args.include_lens}
    print("The write stage changes these settings one at a time and puts each one back:", file=sys.stderr)
    for name, original in planned.items():
        first = next((value for value in SETTINGS[name] if value != original), None)
        print(f"  {name}: {original} -> {first} -> {original}", file=sys.stderr)
    if not confirm(args, "Go ahead?"):
        report.add("write", "settings", SKIP, "declined at the prompt")
        return
    for name, original in values.items():
        if name not in planned:
            report.add("write", name, SKIP, "moves the lens; pass --include-lens to test it")
            continue
        write_one(args, report, projector, name, original)


def write_one(args: argparse.Namespace, report: Report, projector: Projector, name: str, original: str) -> None:
    candidates = [value for value in SETTINGS[name] if value != original][: args.write_attempts]
    refused: list[str] = []
    report.pending[name] = original
    try:
        for candidate in candidates:
            change = report.announce(name, original, candidate)
            _, err, ms = timed(partial(projector.set_setting, name, candidate))
            if isinstance(err, SDCPCommandError):
                change.outcome = f"refused: {err.error_message}"
                refused.append(f"{candidate} ({err.error_message})")
                continue
            if err is not None:
                change.outcome = f"failed: {describe(err)}"
                report.add("write", name, FAIL, f"set {candidate}: {describe(err)}", ms)
                return
            time.sleep(args.settle)
            readback, err, read_ms = timed(lambda: projector.get_setting(name))
            if err is not None:
                change.outcome = "accepted, read back failed"
                report.add("write", name, FAIL, f"set {candidate} ok, read back failed: {describe(err)}", read_ms)
            elif readback == candidate:
                change.outcome = "applied"
                report.add("write", name, PASS, f"{original} → {candidate} → read back {readback}", ms)
            else:
                change.outcome = f"accepted, but read back {readback}"
                report.add("write", name, WARN, f"set {candidate} accepted but read back {readback!r}", ms)
            return
        detail = "refused " + ", ".join(refused) if refused else "no other value to try"
        report.add("write", name, INFO, detail)
    finally:
        restore(args, report, projector, name, original)


def restore(args: argparse.Namespace, report: Report, projector: Projector, name: str, original: str) -> None:
    """Put ``name`` back to ``original``, retrying; leave it in ``report.pending`` if that fails."""
    problem = "unknown"
    for attempt in range(1, args.restore_attempts + 1):
        try:
            current = projector.get_setting(name)
            if current == original:
                break
            retry = f" (attempt {attempt} of {args.restore_attempts})" if attempt > 1 else ""
            print(f"<<< Restoring {name} from {current} to {original}{retry}", file=sys.stderr, flush=True)
            projector.set_setting(name, original)
            # Same wait as after a change: a projector can take a moment to apply a value.
            time.sleep(args.settle)
            current = projector.get_setting(name)
            if current == original:
                break
            problem = f"read back {current} after restoring"
        except SDCPError as err:
            problem = describe(err)
            time.sleep(args.settle)
    else:
        report.add(
            "write",
            f"restore {name}",
            FAIL,
            f"could not put back {original} after {args.restore_attempts} attempts ({problem}); see the top of the report",
        )
        return
    report.pending.pop(name, None)
    if attempt > 1:
        report.add("write", f"restore {name}", WARN, f"back to {original}, but only on attempt {attempt}")


def stage_ir(args: argparse.Namespace, report: Report, projector: Projector) -> None:
    if not args.ir:
        report.add("ir", "remote keys", SKIP, "pass --ir to open and close the on-screen menu")
        return
    if projector.get_power_status() != "POWER_ON":
        report.add("ir", "remote keys", SKIP, "projector is not POWER_ON")
        return
    if not confirm(args, "Open and close the on-screen menu with simulated remote keys?"):
        report.add("ir", "remote keys", SKIP, "declined at the prompt")
        return
    for number, key in enumerate(("MENU", "CURSOR_DOWN", "CURSOR_UP", "MENU")):
        print(f">>> Pressing the remote key {key}", file=sys.stderr, flush=True)
        _, err, ms = timed(partial(projector.send_ir_command, key))
        if err is not None:
            report.add("ir", key, FAIL, describe(err), ms)
            return
        if number == 0:
            report.pending["ON_SCREEN_MENU"] = "closed"
        elif number == 3:
            report.pending.pop("ON_SCREEN_MENU", None)
        # IR keys get no reply; anything near the TCP timeout means the client waited for one.
        report.add("ir", key, PASS if ms < 1000 else FAIL, f"0x{COMMANDS_IR[key]:04x} sent", ms)
        time.sleep(1.5)
    if args.yes:
        report.add("ir", "menu on screen", INFO, "not confirmed visually (--yes)")
    elif confirm(args, "Did the menu open, move, and close on screen?"):
        report.add("ir", "menu on screen", PASS, "confirmed visually")
    else:
        report.add("ir", "menu on screen", FAIL, "the menu did not react as expected")


def stage_power(args: argparse.Namespace, report: Report, projector: Projector) -> None:
    if not args.power:
        report.add("power", "cycle", SKIP, "pass --power to switch off and on again")
        return
    start = projector.get_power_status()
    if start not in ("STANDBY", "POWER_ON"):
        report.add("power", "cycle", SKIP, f"projector is {start}; run again once it has settled")
        return
    if not confirm(args, f"Projector is {start}. Cycle the power (takes a few minutes)?"):
        report.add("power", "cycle", SKIP, "declined at the prompt")
        return
    began = time.monotonic()
    last_logged: list[str | None] = [None]

    def now() -> float:
        return time.monotonic() - began

    def poll_until(targets: set[str], limit: float) -> tuple[str | None, float]:
        """Poll until a target state; return (state, seconds since start) or (None, now) on timeout."""
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            try:
                state = str(projector.get_power_status())
            except SDCPError as err:
                state = f"error: {type(err).__name__}"
            if state != last_logged[0]:
                report.power_log.append((now(), state))
                print(f"  {now():6.1f} s  {state}", file=sys.stderr, flush=True)
                last_logged[0] = state
            if state in targets:
                return state, now()
            time.sleep(args.poll_interval)
        return None, now()

    def switch(on: bool) -> tuple[BaseException | None, float, float]:
        """Announce and send a power command; return (error, ms, seconds since start)."""
        try:
            current = str(projector.get_power_status())
        except SDCPError:
            current = "unknown"
        change = report.announce("power", current, "on" if on else "off")
        sent_at = now()
        _, err, ms = timed(partial(projector.set_power, on))
        change.outcome = "accepted" if err is None else describe(err)
        return err, ms, sent_at

    def first_change_after(moment: float, away_from: str) -> float | None:
        """Seconds after ``moment`` until the log first shows a state other than ``away_from``."""
        for seconds, state in report.power_log:
            if seconds > moment and state != away_from:
                return seconds - moment
        return None

    def turn_on() -> None:
        err, ms, sent_at = switch(True)
        if err is not None:
            report.add("power", "power on", FAIL, describe(err), ms)
            return
        reached, at = poll_until({"POWER_ON"}, args.power_limit)
        if not reached:
            report.add("power", "power on", FAIL, f"never reached POWER_ON in {args.power_limit:.0f} s", ms)
            return
        report.add("power", "power on", PASS, f"POWER_ON {at - sent_at:.1f} s after the command", ms)
        lag = first_change_after(sent_at, "STANDBY")
        if lag is not None and lag > max(2 * args.poll_interval, 1.0):
            report.add("power", "status after power on", INFO, f"still reported STANDBY for {lag:.1f} s after the command")

    def turn_off() -> None:
        err, ms, sent_at = switch(False)
        if err is not None:
            report.add("power", "power off", FAIL, describe(err), ms)
            return
        state, at = poll_until({"COOLING", "COOLING2", "STANDBY"}, args.power_limit)
        if state in ("COOLING", "COOLING2"):
            on_err, on_ms, on_at = switch(True)
            if isinstance(on_err, SDCPCommandError):
                detail = f"refused: {on_err.error_message} (0x{on_err.error_code or 0:x})"
                report.add("power", "power on while cooling", INFO, detail, on_ms)
            elif on_err is not None:
                report.add("power", "power on while cooling", FAIL, describe(on_err), on_ms)
            state, at = poll_until({"STANDBY", "POWER_ON"}, args.power_limit)
            if on_err is None and state == "POWER_ON":
                left = first_change_after(on_at, state_at(on_at))
                when = f"left {state_at(on_at)} after {left:.1f} s and " if left is not None else ""
                detail = f"accepted: {when}reached POWER_ON {at - on_at:.1f} s after the command"
                report.add("power", "power on while cooling", INFO, detail, on_ms)
                err, ms, sent_at = switch(False)
                state, at = poll_until({"STANDBY"}, args.power_limit)
            elif on_err is None:
                report.add("power", "power on while cooling", INFO, f"accepted, but the projector went on to {state}", on_ms)
        if state == "STANDBY":
            report.add("power", "power off", PASS, f"STANDBY {at - sent_at:.1f} s after the command", ms)
        else:
            report.add("power", "power off", FAIL, f"ended in {state}", ms)

    def state_at(moment: float) -> str:
        """The last state logged at or before ``moment``."""
        current = "unknown"
        for seconds, state in report.power_log:
            if seconds > moment:
                break
            current = state
        return current

    def report_silences() -> None:
        """Turn stretches where the projector did not answer into INFO rows."""
        log = report.power_log
        for index, (seconds, state) in enumerate(log):
            if state.startswith("error:") and index + 1 < len(log):
                gap = log[index + 1][0] - seconds
                report.add("power", "no answer", INFO, f"{state[7:]} at {seconds:.1f} s; answered again {gap:.1f} s later")

    report.pending["POWER"] = start
    if start == "STANDBY":
        turn_on()
        turn_off()
    else:
        turn_off()
        turn_on()
    report_silences()
    final = projector.get_power_status()
    if final == start:
        report.pending.pop("POWER", None)
    else:
        report.add("power", "back to start", FAIL, f"started {start}, ended {final}; see the top of the report")


def stage_soak(args: argparse.Namespace, report: Report) -> None:
    if not args.soak:
        report.add("soak", "concurrent polling", SKIP, "pass --soak SECONDS to poll from two threads at once")
        return
    print(f"Soak test for {args.soak:.0f} s ...", file=sys.stderr, flush=True)
    latencies: list[float] = []
    errors: Counter[str] = Counter()
    lock = threading.Lock()
    deadline = time.monotonic() + args.soak

    def worker(func_name: str, interval: float) -> None:
        projector = make_projector(args)
        while time.monotonic() < deadline:
            _, err, ms = timed(getattr(projector, func_name))
            with lock:
                latencies.append(ms)
                if err is not None:
                    errors[f"{func_name}: {type(err).__name__}: {err}"] += 1
            time.sleep(interval)

    threads = [
        threading.Thread(target=worker, args=("get_power_status", 2.0)),
        threading.Thread(target=worker, args=("get_input", 0.7)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    total = len(latencies)
    p95 = statistics.quantiles(latencies, n=20)[-1] if total >= 20 else max(latencies, default=0.0)
    summary = f"{total} calls, {sum(errors.values())} errors, max {max(latencies, default=0):.0f} ms, p95 {p95:.0f} ms"
    report.add("soak", "concurrent polling", PASS if not errors else WARN, summary)
    for message, count in errors.most_common(5):
        report.add("soak", "error", INFO, f"{count} x {message}")


def render(args: argparse.Namespace, report: Report) -> str:
    lines = [
        f"## Hardware check: {report.model or 'unknown model'}",
        "",
        f"- Date: {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"- Projector: {args.ip or 'not found'}:{args.port}, community {args.community!r}, serial {report.serial}",
        f"- IP address: {'found via SDAP' if args.discovered else 'given with --ip'}",
        f"- Client: Python {platform.python_version()} on {platform.system()} {platform.release()}",
        f"- Options: {' '.join(sys.argv[1:])}",
        "",
        *manual_section(args, report),
        "| | Stage | Check | Result | Time |",
        "|---|---|---|---|---|",
    ]
    for r in report.results:
        timing = f"{r.ms:.0f} ms" if r.ms is not None else ""
        detail = r.detail.replace("|", "\\|")
        lines.append(f"| {ICONS[r.status]} | {r.stage} | {r.name} | {detail} | {timing} |")
    if report.changes:
        lines += ["", "Changes made, in order:", ""]
        for change in report.changes:
            outcome = change.outcome
            if change.what in SETTINGS and not outcome.startswith("refused"):
                back = "NOT put back" if change.what in report.pending else f"put back to {change.old}"
                outcome = f"{outcome}, {back}"
            lines.append(f"- {change.what}: {change.old} → {change.new} ({outcome})")
    if report.power_log:
        lines += ["", "Power state timeline:", ""]
        lines += [f"- {seconds:6.1f} s: {state}" for seconds, state in report.power_log]
    if report.notes:
        lines += ["", "Notes:", ""] + [f"- {note}" for note in report.notes]
    counts = Counter(r.status for r in report.results)
    lines += ["", "Summary: " + ", ".join(f"{counts[s]} {s}" for s in (PASS, WARN, FAIL, INFO, SKIP)), ""]
    text = "\n".join(lines)
    if args.redact:
        if args.ip:
            report.private.setdefault(args.ip, "<projector-ip>")
        # Longest first, so a serial that contains another one is replaced whole.
        for value in sorted(report.private, key=len, reverse=True):
            text = text.replace(value, report.private[value])
    return text


MANUAL_HINTS = {
    "POWER": "turn the projector {original_lower} again",
    "ON_SCREEN_MENU": "close the on-screen menu (press MENU on the remote, or wait for it to time out)",
}


def restore_command(args: argparse.Namespace, what: str, original: str) -> str:
    """The CLI command that puts ``what`` back to ``original`` by hand."""
    command = f"poetry run python -m pysdcp_extended --ip {args.ip}"
    if args.port != DEFAULT_TCP_PORT:
        command += f" --port {args.port}"
    if args.community not in (None, "SONY"):
        command += f" --community {args.community}"
    if what == "POWER":
        return command + (" power off" if original == "STANDBY" else " power on")
    if what == "ON_SCREEN_MENU":
        return command + " ir MENU"
    return command + f" set {what} {original}"


def manual_steps(args: argparse.Namespace, report: Report) -> list[tuple[str, str]]:
    """(what to do, command) for everything that was changed and is not confirmed back."""
    steps = []
    for what, original in report.pending.items():
        if what in MANUAL_HINTS:
            text = MANUAL_HINTS[what].format(original_lower="off" if original == "STANDBY" else "on")
        else:
            text = f"set {what} back to {original}"
        steps.append((text, restore_command(args, what, original)))
    return steps


def manual_section(args: argparse.Namespace, report: Report) -> list[str]:
    steps = manual_steps(args, report)
    if not steps:
        return []
    lines = [
        "### ⚠️ Set these back by hand",
        "",
        "The run changed these and could not confirm they are back to how they were.",
        "Use the projector's menu, or run the command shown:",
        "",
    ]
    for text, command in steps:
        lines += [f"- {text}:", f"  `{command}`"]
    return [*lines, ""]


def finish(args: argparse.Namespace, report: Report) -> int:
    if args.community is None:
        args.community = "SONY"
    text = render(args, report)
    print(text)
    if args.output:
        path = Path(args.output)
    else:
        model = (report.model or "projector").replace(" ", "_")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = REPORTS_DIR / f"hardware-report-{model}-{stamp}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"\nReport written to {path}", file=sys.stderr)
    steps = manual_steps(args, report)
    if steps:
        print("\n!!! The projector was left changed. Set these back by hand:", file=sys.stderr)
        for step, command in steps:
            print(f"!!!   {step}:\n!!!     {command}", file=sys.stderr)
    if report.interrupted:
        return 130
    return 1 if report.failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ip", help="projector IP address (default: find it via SDAP)")
    parser.add_argument("--community", help="PJ Talk community (default: the advertised one, else SONY)")
    parser.add_argument("--port", type=int, default=DEFAULT_TCP_PORT, help=f"SDCP TCP port (default: {DEFAULT_TCP_PORT})")
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT, help=f"SDAP UDP port (default: {DEFAULT_UDP_PORT})")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TCP_TIMEOUT, help="TCP timeout in seconds (default: 2)")
    parser.add_argument("--skip-discovery", action="store_true", help="do not listen for SDAP")
    parser.add_argument("--discovery-timeout", type=float, default=35.0, help="seconds to listen for SDAP (default: 35)")
    parser.add_argument("--write", action="store_true", help="change each setting, read it back, restore it")
    parser.add_argument("--include-lens", action="store_true", help="with --write, also change PICTURE_POSITION")
    parser.add_argument("--write-attempts", type=int, default=2, help="values to try per setting before giving up (default: 2)")
    parser.add_argument("--settle", type=float, default=1.0, help="seconds between a write and its read back (default: 1)")
    parser.add_argument("--restore-attempts", type=int, default=3, help="tries to put each setting back (default: 3)")
    parser.add_argument("--ir", action="store_true", help="open and close the on-screen menu")
    parser.add_argument("--power", action="store_true", help="switch the projector off and on again")
    parser.add_argument("--power-limit", type=float, default=300.0, help="seconds to wait for each power transition")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="seconds between power polls (default: 1)")
    parser.add_argument("--soak", type=float, default=0.0, metavar="SECONDS", help="poll from two threads for this long")
    parser.add_argument("--yes", action="store_true", help="do not ask before writes and power changes")
    parser.add_argument("--redact", action="store_true", help="hide IP addresses, serial numbers and locations in the report")
    parser.add_argument("--output", help="report file (default: test_reports/hardware-report-<model>-<date>_<time>.md)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.ip is None and args.skip_discovery:
        parser.error("--skip-discovery needs --ip")
    args.discovered = args.ip is None
    report = Report()
    try:
        run_stages(args, report)
    except KeyboardInterrupt:
        report.interrupted = True
        report.add(report.stage, "run", INFO, "stopped with Ctrl+C")
        report.notes.append(f"The run was stopped with Ctrl+C during the {report.stage} stage; later stages did not run.")
    except Exception as err:  # a bug in this script or the library; still write the report
        report.add(report.stage, "run", FAIL, f"crashed: {describe(err)}")
        report.notes.append("Traceback:\n\n```\n" + traceback.format_exc().rstrip() + "\n```")
    return finish(args, report)


def run_stages(args: argparse.Namespace, report: Report) -> None:
    report.stage = "discovery"
    stage_discovery(args, report)
    if args.community is None:
        args.community = "SONY"
    projector = make_projector(args)
    report.stage = "read"
    values = stage_read(args, report, projector)
    report.stage = "faults"
    stage_faults(args, report)
    report.stage = "write"
    stage_write(args, report, projector, values)
    report.stage = "ir"
    stage_ir(args, report, projector)
    report.stage = "power"
    stage_power(args, report, projector)
    report.stage = "soak"
    stage_soak(args, report)


if __name__ == "__main__":
    raise SystemExit(main())
