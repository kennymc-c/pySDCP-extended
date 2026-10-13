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
    assert "(this projector)" in text
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
