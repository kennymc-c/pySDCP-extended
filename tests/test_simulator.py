"""The simulator's own request handling, exercised without sockets."""

from __future__ import annotations

import time

import pytest

from pysdcp_extended import (
    Header,
    Projector,
    SDCPCommandError,
    SDCPProtocolError,
    create_command_buffer,
    process_command_response,
)
from pysdcp_extended.protocol import ACTIONS, COMMANDS, COMMANDS_IR, INPUTS, POWER_STATUS
from pysdcp_extended.simulator import (
    ERROR_DIFFERENT_COMMUNITY,
    ERROR_INVALID_DATA,
    ERROR_INVALID_ITEM,
    ERROR_INVALID_ITEM_REQUEST,
    ERROR_INVALID_LENGTH,
    ERROR_INVALID_VERSION,
    ProjectorSimulator,
    SimulatorState,
    build_response,
    handle_request,
)
from tests.conftest import FAST_TIMEOUT

HEADER = Header(version=2, category=0x0A, community="SONY")


def _request(action: int, command: int, data: int | None = None, header: Header = HEADER) -> bytes:
    return bytes(create_command_buffer(header, action, command, data))


def _error_code(reply: bytes | None) -> int | None:
    assert reply is not None
    _, ok, _, data = process_command_response(reply)
    assert ok is False
    return data


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        pytest.param(b"\x02\x0aSONY", ERROR_INVALID_LENGTH, id="short-request"),
        pytest.param(b"\x01" + _request(ACTIONS["GET"], COMMANDS["INPUT"])[1:], ERROR_INVALID_VERSION, id="wrong-version"),
        pytest.param(
            _request(ACTIONS["GET"], COMMANDS["INPUT"], header=Header(2, 0x0A, "ROOM")),
            ERROR_DIFFERENT_COMMUNITY,
            id="wrong-community",
        ),
        pytest.param(_request(ACTIONS["GET"], 0x0FFF), ERROR_INVALID_ITEM, id="unknown-command"),
        pytest.param(_request(0x07, COMMANDS["INPUT"]), ERROR_INVALID_ITEM_REQUEST, id="unknown-action"),
        pytest.param(_request(ACTIONS["GET"], COMMANDS["SET_POWER"]), ERROR_INVALID_ITEM_REQUEST, id="get-of-set-only-command"),
        pytest.param(_request(ACTIONS["SET"], COMMANDS["INPUT"]), ERROR_INVALID_LENGTH, id="set-without-data"),
        pytest.param(_request(ACTIONS["SET"], COMMANDS["SET_POWER"], 7), ERROR_INVALID_DATA, id="set-power-bad-value"),
        pytest.param(
            _request(ACTIONS["SET"], COMMANDS["GET_STATUS_POWER"], 1), ERROR_INVALID_ITEM_REQUEST, id="set-of-get-only-command"
        ),
        pytest.param(_request(ACTIONS["SET"], COMMANDS["INPUT"], 0x99), ERROR_INVALID_DATA, id="set-bad-value"),
    ],
)
def test_handle_request_error_codes(frame: bytes, expected: int) -> None:
    assert _error_code(handle_request(SimulatorState(), frame)) == expected


def test_handle_request_ir_command_gets_no_reply() -> None:
    state = SimulatorState()
    assert handle_request(state, _request(ACTIONS["SET"], COMMANDS_IR["MENU"])) is None
    assert state.last is not None
    assert state.last.name == "MENU"


def test_handle_request_get_status_error() -> None:
    state = SimulatorState(error_status=2)
    reply = handle_request(state, _request(ACTIONS["GET"], COMMANDS["GET_STATUS_ERROR"]))
    assert reply is not None
    assert process_command_response(reply)[3] == 2


def test_handle_request_set_and_get_round_trip() -> None:
    state = SimulatorState()
    assert handle_request(state, _request(ACTIONS["SET"], COMMANDS["INPUT"], INPUTS["HDMI2"])) == build_response(
        "SONY", True, COMMANDS["INPUT"], None
    )
    assert handle_request(state, _request(ACTIONS["GET"], COMMANDS["INPUT"])) == build_response(
        "SONY", True, COMMANDS["INPUT"], INPUTS["HDMI2"]
    )
    assert state.received[-1].name == "INPUT"
    assert state.received[0].name == "INPUT"
    state.set("HDR", "ON")
    assert state.get("HDR") == 1


def test_state_helpers_for_unknown_command_name() -> None:
    state = SimulatorState()
    reply = handle_request(state, _request(ACTIONS["GET"], 0x0FFF))
    assert reply is not None
    assert state.last is not None
    assert state.last.name == "0x0fff"


def test_power_on_reports_start_up_then_client_sees_it(projector: Projector, state: SimulatorState) -> None:
    projector.set_power(True)
    assert state.power == POWER_STATUS["START_UP"]


def test_success_reply_without_data_where_data_is_required(projector: Projector, state: SimulatorState) -> None:
    state.reply_bytes = build_response("SONY", True, COMMANDS["GET_STATUS_LAMP_TIMER"], None)
    with pytest.raises(SDCPProtocolError, match="Expected data"):
        projector.get_lamp_timer()


def test_command_error_without_code() -> None:
    err = SDCPCommandError(COMMANDS["INPUT"], None)
    assert err.error_code is None
    assert err.error_message == "No error code in response"


def test_simulator_honours_custom_community(state: SimulatorState) -> None:
    state.community = "ROOM"
    with ProjectorSimulator("127.0.0.1", 0, state) as sim:
        assert Projector(ip=sim.host, tcp_port=sim.port, community="ROOM", tcp_timeout=FAST_TIMEOUT).get_power() is False


def test_power_timing_warm_up_and_cool_down(projector: Projector, state: SimulatorState) -> None:
    state.warm_up_seconds = 0.2
    state.cool_down_seconds = 0.2
    projector.set_power(True)
    assert projector.get_power_status() == "START_UP"
    time.sleep(0.25)
    assert projector.get_power_status() == "POWER_ON"
    projector.set_power(True)
    assert projector.get_power_status() == "POWER_ON"
    projector.set_power(False)
    assert projector.get_power_status() == "COOLING"
    assert projector.get_power() is False
    time.sleep(0.25)
    assert projector.get_power_status() == "STANDBY"


def test_apply_delay_keeps_the_old_value_for_a_while(projector: Projector, state: SimulatorState) -> None:
    state.apply_seconds = 0.2
    projector.set_setting("HDR", "AUTO")
    assert projector.get_setting("HDR") == "OFF"
    time.sleep(0.25)
    assert projector.get_setting("HDR") == "AUTO"
