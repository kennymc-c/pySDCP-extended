"""Run scripts/hardware_check.py end to end against the simulator."""

from __future__ import annotations

import importlib.util
import itertools
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pysdcp_extended import Projector, SDCPTimeoutError
from pysdcp_extended.protocol import POWER_STATUS
from pysdcp_extended.simulator import ProjectorSimulator, SDAPBroadcaster, SimulatorState

SCRIPT = Path(__file__).parent.parent / "scripts" / "hardware_check.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hardware_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["hardware_check"] = module
    spec.loader.exec_module(module)
    return module


hardware_check = _load()


def _args(sim: ProjectorSimulator, report: Path, *extra: str) -> list[str]:
    return [
        "--ip",
        sim.host,
        "--port",
        str(sim.port),
        "--timeout",
        "0.5",
        "--settle",
        "0",
        "--poll-interval",
        "0.05",
        "--power-limit",
        "5",
        "--output",
        str(report),
        *extra,
    ]


@pytest.mark.parametrize(
    "start",
    [pytest.param(POWER_STATUS["POWER_ON"], id="starts-on"), pytest.param(POWER_STATUS["STANDBY"], id="starts-in-standby")],
)
def test_full_run_passes_and_restores_everything(tmp_path: Path, start: int) -> None:
    state = SimulatorState(power=start, warm_up_seconds=0.2, cool_down_seconds=0.3)
    before = dict(state.settings)
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=state) as sim:
        code = hardware_check.main(_args(sim, report, "--skip-discovery", "--write", "--ir", "--power", "--soak", "1", "--yes"))
    text = report.read_text()
    assert code == 0, text
    assert "❌" not in text
    assert state.settings == before
    assert state.power == start
    assert "power on while cooling" in text
    assert "Power state timeline" in text


def test_writes_are_skipped_in_standby(tmp_path: Path) -> None:
    state = SimulatorState()
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=state) as sim:
        code = hardware_check.main(_args(sim, report, "--skip-discovery", "--write", "--yes"))
    assert code == 0
    assert "projector is not POWER_ON" in report.read_text()
    assert all(item.action != 0x00 for item in state.received)


def test_discovery_and_redaction(tmp_path: Path, free_udp_port: int) -> None:
    state = SimulatorState(power=POWER_STATUS["POWER_ON"], serial_number=7654321)
    report = tmp_path / "report.md"
    with (
        ProjectorSimulator(state=state) as sim,
        SDAPBroadcaster(state, ("127.0.0.1", free_udp_port), interval=0.1),
    ):
        code = hardware_check.main(_args(sim, report, "--udp-port", str(free_udp_port), "--discovery-timeout", "0.5", "--redact"))
    text = report.read_text()
    assert code == 0, text
    assert "advertisement from <projector-ip>" in text
    assert "VPL-VW520" in text
    assert "7654321" not in text
    assert "127.0.0.1" not in text
    assert "Theater" not in text
    assert "<location>" in text


def test_unreachable_projector_fails(tmp_path: Path, free_tcp_port: int) -> None:
    report = tmp_path / "report.md"
    args = ["--ip", "127.0.0.1", "--port", str(free_tcp_port), "--timeout", "0.5", "--skip-discovery", "--output", str(report)]
    with pytest.raises(SystemExit) as excinfo:
        hardware_check.main(args)
    assert excinfo.value.code == 1
    assert "did not answer the first command" in report.read_text()


def test_unsupported_setting_is_info_not_failure(tmp_path: Path) -> None:
    state = SimulatorState(power=POWER_STATUS["POWER_ON"], fail_with={0x007C: 0x180})
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=state) as sim:
        code = hardware_check.main(_args(sim, report, "--skip-discovery", "--write", "--yes"))
    text = report.read_text()
    assert code == 0, text
    assert "setting HDR | not available now: Item Error: Not Applicable Item" in text


def _discovery_args(sim: ProjectorSimulator, udp_port: int, report: Path, *extra: str) -> list[str]:
    return [
        "--port",
        str(sim.port),
        "--udp-port",
        str(udp_port),
        "--discovery-timeout",
        "0.5",
        "--timeout",
        "0.5",
        "--output",
        str(report),
        *extra,
    ]


def test_finds_the_ip_and_community_without_being_told(tmp_path: Path, free_udp_port: int) -> None:
    # A non-default community proves the advertised one was used: "SONY" would be rejected.
    state = SimulatorState(power=POWER_STATUS["POWER_ON"], community="ROOM", product_name="VPL-VW295ES")
    report = tmp_path / "report.md"
    with (
        ProjectorSimulator(state=state) as sim,
        SDAPBroadcaster(state, ("127.0.0.1", free_udp_port), interval=0.1),
    ):
        code = hardware_check.main(_discovery_args(sim, free_udp_port, report))
    text = report.read_text()
    assert code == 0, text
    assert "using 127.0.0.1, found via SDAP" in text
    assert "using 'ROOM' from the advertisement" in text
    assert "found 127.0.0.1 by itself and read power POWER_ON" in text
    assert "IP address: found via SDAP" in text
    assert "## Hardware check: VPL-VW295ES" in text


def test_warns_when_given_community_differs_from_advertised(tmp_path: Path, free_udp_port: int) -> None:
    state = SimulatorState(power=POWER_STATUS["POWER_ON"], community="ROOM")
    report = tmp_path / "report.md"
    with (
        ProjectorSimulator(state=state) as sim,
        SDAPBroadcaster(state, ("127.0.0.1", free_udp_port), interval=0.1),
        pytest.raises(SystemExit),
    ):
        hardware_check.main(_discovery_args(sim, free_udp_port, report, "--ip", "127.0.0.1", "--community", "SONY"))
    assert "projector advertises 'ROOM' but --community is 'SONY'" in report.read_text()


def test_nothing_heard_without_ip_fails_with_tips(tmp_path: Path, free_udp_port: int) -> None:
    report = tmp_path / "report.md"
    with ProjectorSimulator() as sim, pytest.raises(SystemExit) as excinfo:
        hardware_check.main(_discovery_args(sim, free_udp_port, report))
    assert excinfo.value.code == 1
    text = report.read_text()
    assert "no advertisement heard" in text
    assert "same network segment" in text
    assert "none was discovered" in text


def _two_projectors(udp_port: int) -> tuple[SimulatorState, SimulatorState, SDAPBroadcaster, SDAPBroadcaster]:
    first = SimulatorState(power=POWER_STATUS["POWER_ON"], serial_number=1, product_name="VPL-VW285")
    second = SimulatorState(power=POWER_STATUS["POWER_ON"], serial_number=2, product_name="VPL-VW295ES")
    target = ("127.0.0.1", udp_port)
    return first, second, SDAPBroadcaster(first, target, interval=0.1), SDAPBroadcaster(second, target, interval=0.1)


def test_several_projectors_with_yes_asks_for_ip(tmp_path: Path, free_udp_port: int) -> None:
    first, _, one, two = _two_projectors(free_udp_port)
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=first) as sim, one, two, pytest.raises(SystemExit) as excinfo:
        hardware_check.main(_discovery_args(sim, free_udp_port, report, "--yes"))
    assert excinfo.value.code == 1
    assert "2 projectors heard; pass --ip to pick one" in report.read_text()


def test_several_projectors_prompts_for_a_choice(tmp_path: Path, free_udp_port: int, monkeypatch: pytest.MonkeyPatch) -> None:
    _, second, one, two = _two_projectors(free_udp_port)
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=second) as sim, one, two:
        code = hardware_check.main(_discovery_args(sim, free_udp_port, report))
    text = report.read_text()
    assert code == 0, text
    assert "## Hardware check: VPL-VW295ES" in text
    assert "serial 2" in text


def test_skip_discovery_needs_an_ip() -> None:
    with pytest.raises(SystemExit) as excinfo:
        hardware_check.main(["--skip-discovery"])
    assert excinfo.value.code == 2


def test_reports_default_to_the_ignored_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hardware_check, "REPORTS_DIR", tmp_path / "test_reports")
    with ProjectorSimulator() as sim:
        args = ["--ip", sim.host, "--port", str(sim.port), "--timeout", "0.5", "--skip-discovery"]
        assert hardware_check.main(args) == 0
        assert hardware_check.main(args) == 0
    reports = sorted((tmp_path / "test_reports").glob("hardware-report-projector-*.md"))
    assert len(reports) >= 1
    assert "## Hardware check" in reports[0].read_text()


def test_reports_folder_is_git_ignored() -> None:
    assert hardware_check.REPORTS_DIR.name == "test_reports"
    assert hardware_check.REPORTS_DIR.parent == SCRIPT.resolve().parent.parent
    assert "test_reports/" in (SCRIPT.parent.parent / ".gitignore").read_text().splitlines()


ORIGINAL_SET_SETTING = Projector.set_setting


def _on_state(**kwargs: Any) -> SimulatorState:
    return SimulatorState(power=POWER_STATUS["POWER_ON"], **kwargs)


def _write_args(sim: ProjectorSimulator, report: Path, *extra: str) -> list[str]:
    return _args(sim, report, "--skip-discovery", "--write", "--yes", *extra)


def _patch_set_setting(
    monkeypatch: pytest.MonkeyPatch, when: Callable[[str, str], bool], effect: Callable[[], None], apply_first: bool = False
) -> list[tuple[str, str]]:
    """Make set_setting run ``effect`` (raise) for matching calls; return every call made."""
    calls: list[tuple[str, str]] = []

    def patched(self: Projector, name: str, value: str) -> bool:
        calls.append((name, value))
        if when(name, value):
            if apply_first:
                ORIGINAL_SET_SETTING(self, name, value)
            effect()
        return ORIGINAL_SET_SETTING(self, name, value)

    monkeypatch.setattr(Projector, "set_setting", patched)
    return calls


def _raise(exc: BaseException) -> Callable[[], None]:
    def effect() -> None:
        raise exc

    return effect


def test_changes_are_announced_and_logged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=_on_state()) as sim:
        assert hardware_check.main(_write_args(sim, report)) == 0
    err = capsys.readouterr().err
    assert "  HDR: OFF -> ON -> OFF" in err
    assert ">>> Changing HDR from OFF to ON" in err
    assert "<<< Restoring HDR from ON to OFF" in err
    text = report.read_text()
    assert "- HDR: OFF → ON (applied, put back to OFF)" in text
    assert "Set these back by hand" not in text
    assert "!!!" not in err


def test_restore_that_keeps_failing_is_listed_for_manual_fixing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_set_setting(monkeypatch, lambda n, v: (n, v) == ("HDR", "OFF"), _raise(SDCPTimeoutError("no answer")))
    state = _on_state()
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=state) as sim:
        code = hardware_check.main(_write_args(sim, report, "--redact"))
    assert code == 1
    assert calls.count(("HDR", "OFF")) == 3
    assert state.get("HDR") == 1
    text = report.read_text()
    # The manual section comes before the results table.
    assert text.index("Set these back by hand") < text.index("| | Stage |")
    assert "- set HDR back to OFF:" in text
    assert "`poetry run python -m pysdcp_extended --ip <projector-ip> --port" in text
    assert "set HDR OFF`" in text
    assert "could not put back OFF after 3 attempts" in text
    assert "- HDR: OFF → ON (applied, NOT put back)" in text
    err = capsys.readouterr().err
    assert "!!! The projector was left changed" in err
    assert f"--ip 127.0.0.1 --port {sim.port} set HDR OFF" in err


def test_restore_that_succeeds_on_retry_is_only_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failures = iter([True])
    _patch_set_setting(
        monkeypatch,
        lambda n, v: (n, v) == ("HDR", "OFF") and next(failures, False),
        _raise(SDCPTimeoutError("no answer")),
    )
    state = _on_state()
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=state) as sim:
        assert hardware_check.main(_write_args(sim, report)) == 0
    assert state.get("HDR") == 0
    text = report.read_text()
    assert "back to OFF, but only on attempt 2" in text
    assert "Set these back by hand" not in text


def test_slow_projector_is_not_mistaken_for_a_failed_restore(tmp_path: Path) -> None:
    state = _on_state(apply_seconds=0.15)
    before = dict(state.settings)
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=state) as sim:
        args = [a if a != "0" else "0.25" for a in _write_args(sim, report)]  # --settle 0 -> 0.25
        code = hardware_check.main(args)
    text = report.read_text()
    assert code == 0, text
    assert "❌" not in text
    time.sleep(0.2)
    state.apply_due()
    assert state.settings == before


def test_ctrl_c_after_a_change_still_restores_and_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_set_setting(monkeypatch, lambda n, v: (n, v) == ("HDR", "ON"), _raise(KeyboardInterrupt()), apply_first=True)
    state = _on_state()
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=state) as sim:
        code = hardware_check.main(_write_args(sim, report))
    assert code == 130
    assert state.get("HDR") == 0
    text = report.read_text()
    assert "stopped with Ctrl+C during the write stage" in text
    assert "Set these back by hand" not in text


def test_ctrl_c_during_a_restore_lists_the_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_set_setting(monkeypatch, lambda n, v: (n, v) == ("HDR", "OFF"), _raise(KeyboardInterrupt()))
    state = _on_state()
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=state) as sim:
        code = hardware_check.main(_write_args(sim, report))
    assert code == 130
    assert state.get("HDR") == 1
    text = report.read_text()
    assert "- set HDR back to OFF:" in text
    assert "MOTIONFLOW" not in text.split("| | Stage |")[0]


def test_failing_remote_key_leaves_a_note_to_close_the_menu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Projector.send_ir_command

    def fail_on_cursor(self: Projector, name: str) -> bool:
        if name == "CURSOR_DOWN":
            raise RuntimeError("boom")
        return original(self, name)

    monkeypatch.setattr(Projector, "send_ir_command", fail_on_cursor)
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=_on_state()) as sim:
        code = hardware_check.main(_args(sim, report, "--skip-discovery", "--ir", "--yes"))
    assert code == 1
    text = report.read_text()
    assert "| ir | CURSOR_DOWN | RuntimeError: boom |" in text
    assert "close the on-screen menu" in text
    assert " ir MENU`" in text


def test_crash_still_writes_a_report_with_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Projector.get_power_status
    calls = iter(range(100))

    def crash_on_second_call(self: Projector) -> str | int:
        # The first call is the read stage's; the second is the write stage's check, outside any wrapper.
        if next(calls) == 1:
            raise RuntimeError("boom")
        return original(self)

    monkeypatch.setattr(Projector, "get_power_status", crash_on_second_call)
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=_on_state()) as sim:
        code = hardware_check.main(_write_args(sim, report))
    assert code == 1
    text = report.read_text()
    assert "| write | run | crashed: RuntimeError: boom |" in text
    assert "Traceback" in text


def test_ctrl_c_during_power_cycle_says_how_to_get_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Projector.set_power
    calls = iter(range(100))

    def interrupt_second_call(self: Projector, on: bool = True) -> bool:
        if next(calls) == 1:
            raise KeyboardInterrupt
        return original(self, on)

    monkeypatch.setattr(Projector, "set_power", interrupt_second_call)
    state = _on_state(warm_up_seconds=0.2, cool_down_seconds=0.3)
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=state) as sim:
        code = hardware_check.main(_args(sim, report, "--skip-discovery", "--power", "--yes"))
    assert code == 130
    text = report.read_text()
    assert "- turn the projector on again:" in text
    assert " power on`" in text
    assert "- power: POWER_ON → off (accepted)" in text


def test_power_report_matches_what_a_vw295es_does(tmp_path: Path) -> None:
    # Timings scaled down from a real VPL-VW295ES run: it kept reporting STANDBY for a while after
    # the on command, went silent briefly after the off command, and went straight back to warming
    # up when switched on during cool-down.
    state = SimulatorState(
        start_delay_seconds=1.3,
        warm_up_seconds=0.3,
        cool_down_seconds=0.8,
        silent_after_off_seconds=0.4,
    )
    report = tmp_path / "report.md"
    with ProjectorSimulator(state=state) as sim:
        args = _args(sim, report, "--skip-discovery", "--power", "--yes")
        args[args.index("--timeout") + 1] = "0.2"
        code = hardware_check.main(args)
    text = report.read_text()
    assert code == 0, text
    assert state.power == POWER_STATUS["STANDBY"]
    assert "| power | status after power on | still reported STANDBY for" in text
    assert "| power | no answer | SDCPTimeoutError at" in text
    assert "| power | power on while cooling | accepted: left COOLING" in text
    assert "was queued" not in text
    timeline = [line.split(": ", 1)[1] for line in text.split("Power state timeline:")[1].split("\n\n")[1].splitlines()]
    assert all(a != b for a, b in itertools.pairwise(timeline)), timeline
