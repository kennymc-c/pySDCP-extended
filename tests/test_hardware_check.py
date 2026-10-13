"""Run scripts/hardware_check.py end to end against the simulator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

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
