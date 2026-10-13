"""A fake Sony projector that speaks SDCP and SDAP, for tests and local development.

Run it from the command line to get something to point a client at::

    python -m pysdcp_extended.simulator                 # SDCP on 0.0.0.0:53484, SDAP broadcasts on 53862
    python -m pysdcp_extended.simulator --port 5348 --no-sdap

Or use it from tests::

    with ProjectorSimulator() as sim:
        projector = Projector(ip="127.0.0.1", tcp_port=sim.port)
        projector.set_power(True)
        assert sim.state.power == POWER_STATUS["START_UP"]

The simulator answers like a VPL-VW series projector: every GET for a known command
returns the stored value, every SET validates the value against the protocol tables
and stores it, IR commands are accepted without a reply, and unknown commands or bad
values get the matching SDCP error code. ``SimulatorState`` lets a test make it
misbehave (forced error codes, no reply, garbage reply).
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import socket
import socketserver
import struct
import threading
import time
from dataclasses import dataclass, field

from pysdcp_extended.protocol import (
    ACTIONS,
    COMMANDS,
    COMMANDS_IR,
    POWER_STATUS,
    SDCP_CATEGORY,
    SDCP_VERSION,
    SETTINGS,
    is_ir_command,
)

_LOGGER = logging.getLogger(__name__)

SDCP_HEADER_LENGTH = 10
SDAP_ID = "DA"
SDAP_INTERVAL = 30.0

ERROR_INVALID_ITEM = 0x101
ERROR_INVALID_ITEM_REQUEST = 0x102
ERROR_INVALID_LENGTH = 0x103
ERROR_INVALID_DATA = 0x104
ERROR_NOT_APPLICABLE_ITEM = 0x180
ERROR_DIFFERENT_COMMUNITY = 0x201
ERROR_INVALID_VERSION = 0x1001

_COMMAND_NAMES = {code: name for name, code in COMMANDS.items()}
_COMMAND_NAMES.update({code: name for name, code in COMMANDS_IR.items()})


@dataclass
class ReceivedCommand:
    """One request the simulator received."""

    action: int
    command: int
    data: int | None

    @property
    def name(self) -> str:
        return _COMMAND_NAMES.get(self.command, f"0x{self.command:04x}")


def _default_settings() -> dict[int, int]:
    return {COMMANDS[name]: next(iter(table.values())) for name, table in SETTINGS.items()}


@dataclass
class SimulatorState:
    """Everything the simulator knows, and the knobs that make it misbehave."""

    community: str = "SONY"
    product_name: str = "VPL-VW520"
    serial_number: int = 1234567
    location: str = "Theater"
    power: int = POWER_STATUS["STANDBY"]
    lamp_hours: int = 123
    error_status: int = 0
    settings: dict[int, int] = field(default_factory=_default_settings)
    received: list[ReceivedCommand] = field(default_factory=list)

    # Misbehaviour knobs
    fail_with: dict[int, int] = field(default_factory=dict)
    """Command code -> SDCP error code to answer with instead of handling it."""
    reply: bool = True
    """When False the simulator reads the request and then never answers."""
    reply_bytes: bytes | None = None
    """When set, this is sent as the reply to every request, as is."""
    hold_seconds: float = 0.0
    """Delay before answering, to exercise client timeouts."""

    # Power timing, to behave like a lamp that warms up and cools down
    warm_up_seconds: float | None = None
    """Seconds from power on until START_UP becomes POWER_ON. None: stays in START_UP."""
    cool_down_seconds: float | None = None
    """Seconds spent in COOLING after power off before STANDBY. None or 0: straight to STANDBY."""
    power_changed_at: float = field(default=0.0, repr=False)

    def current_power(self) -> int:
        """Return the power status, advancing warm-up and cool-down by the clock."""
        elapsed = time.monotonic() - self.power_changed_at
        if self.power == POWER_STATUS["START_UP"] and self.warm_up_seconds is not None and elapsed >= self.warm_up_seconds:
            self.power = POWER_STATUS["POWER_ON"]
        elif self.power == POWER_STATUS["COOLING"] and self.cool_down_seconds is not None and elapsed >= self.cool_down_seconds:
            self.power = POWER_STATUS["STANDBY"]
        return self.power

    def get(self, name: str) -> int:
        """Return the stored value of a setting from ``protocol.SETTINGS`` by name."""
        return self.settings[COMMANDS[name]]

    def set(self, name: str, value: str) -> None:
        """Store a named value for a setting from ``protocol.SETTINGS``."""
        self.settings[COMMANDS[name]] = SETTINGS[name][value]

    @property
    def last(self) -> ReceivedCommand | None:
        return self.received[-1] if self.received else None


def build_sdap_packet(state: SimulatorState) -> bytes:
    """Build one SDAP advertisement for ``state``."""
    return (
        SDAP_ID.encode("ascii")
        + bytes([SDCP_VERSION, SDCP_CATEGORY])
        + state.community.encode("ascii")[:4].ljust(4, b"\x00")
        + state.product_name.encode("ascii")[:12].ljust(12, b"\x00")
        + struct.pack(">I", state.serial_number)
        + struct.pack(">H", state.current_power())
        + state.location.encode("ascii")[:24].ljust(24, b"\x00")
    )


def build_response(community: str, success: bool, command: int, data: int | None) -> bytes:
    """Build one SDCP response frame."""
    body = bytearray(SDCP_HEADER_LENGTH)
    body[0] = SDCP_VERSION
    body[1] = SDCP_CATEGORY
    body[2:6] = community.encode("ascii")[:4].ljust(4, b"\x00")
    body[6] = 1 if success else 0
    struct.pack_into(">H", body, 7, command)
    if data is None:
        body[9] = 0
        return bytes(body)
    body[9] = 2
    return bytes(body) + struct.pack(">H", data)


def handle_request(state: SimulatorState, request: bytes) -> bytes | None:
    """Compute the reply for one request. ``None`` means "do not answer"."""
    if len(request) < SDCP_HEADER_LENGTH:
        return build_response(state.community, False, 0, ERROR_INVALID_LENGTH)
    version = request[0]
    community = request[2:6].decode(errors="replace").strip("\x00")
    action = request[6]
    command = struct.unpack(">H", request[7:9])[0]
    data_len = request[9]
    data = int.from_bytes(request[10 : 10 + data_len], "big") if data_len else None
    state.received.append(ReceivedCommand(action=action, command=command, data=data))
    _LOGGER.info(
        "%s %s data=%s", "GET" if action == ACTIONS["GET"] else "SET", _COMMAND_NAMES.get(command, f"0x{command:04x}"), data
    )

    if state.reply_bytes is not None:
        return state.reply_bytes
    if not state.reply:
        return None
    if version != SDCP_VERSION:
        return build_response(state.community, False, command, ERROR_INVALID_VERSION)
    if community != state.community:
        return build_response(state.community, False, command, ERROR_DIFFERENT_COMMUNITY)
    if command in state.fail_with:
        return build_response(state.community, False, command, state.fail_with[command])
    if is_ir_command(command):
        return None
    if command not in _COMMAND_NAMES:
        return build_response(state.community, False, command, ERROR_INVALID_ITEM)

    if action == ACTIONS["GET"]:
        return _handle_get(state, command)
    if action == ACTIONS["SET"]:
        return _handle_set(state, command, data)
    return build_response(state.community, False, command, ERROR_INVALID_ITEM_REQUEST)


def _handle_get(state: SimulatorState, command: int) -> bytes:
    if command == COMMANDS["GET_STATUS_POWER"]:
        return build_response(state.community, True, command, state.current_power())
    if command == COMMANDS["GET_STATUS_LAMP_TIMER"]:
        return build_response(state.community, True, command, state.lamp_hours)
    if command == COMMANDS["GET_STATUS_ERROR"]:
        return build_response(state.community, True, command, state.error_status)
    if command in state.settings:
        return build_response(state.community, True, command, state.settings[command])
    return build_response(state.community, False, command, ERROR_INVALID_ITEM_REQUEST)


def _handle_set(state: SimulatorState, command: int, data: int | None) -> bytes:
    if data is None:
        return build_response(state.community, False, command, ERROR_INVALID_LENGTH)
    if command == COMMANDS["SET_POWER"]:
        current = state.current_power()
        if data == POWER_STATUS["START_UP"]:
            if current not in (POWER_STATUS["START_UP"], POWER_STATUS["START_UP_LAMP"], POWER_STATUS["POWER_ON"]):
                state.power = POWER_STATUS["START_UP"]
                state.power_changed_at = time.monotonic()
        elif data == POWER_STATUS["STANDBY"]:
            if current != POWER_STATUS["STANDBY"]:
                state.power = POWER_STATUS["COOLING"] if state.cool_down_seconds else POWER_STATUS["STANDBY"]
                state.power_changed_at = time.monotonic()
        else:
            return build_response(state.community, False, command, ERROR_INVALID_DATA)
        return build_response(state.community, True, command, None)
    name = _COMMAND_NAMES[command]
    if name not in SETTINGS:
        return build_response(state.community, False, command, ERROR_INVALID_ITEM_REQUEST)
    if data not in SETTINGS[name].values():
        return build_response(state.community, False, command, ERROR_INVALID_DATA)
    state.settings[command] = data
    return build_response(state.community, True, command, None)


class _SDCPHandler(socketserver.BaseRequestHandler):
    server: ProjectorSimulator

    def handle(self) -> None:
        self.request.settimeout(2)
        try:
            request = self.request.recv(1024)
        except OSError:
            return
        if not request:
            return
        state = self.server.state
        reply = handle_request(state, request)
        if state.hold_seconds:
            time.sleep(state.hold_seconds)
        if reply is None:
            # Keep the connection open until the client gives up, like a silent projector would.
            if not state.reply:
                with contextlib.suppress(OSError):
                    self.request.recv(1)
            return
        with contextlib.suppress(OSError):
            self.request.sendall(reply)


class ProjectorSimulator(socketserver.ThreadingTCPServer):
    """Fake projector listening for SDCP on ``host``:``port`` (port 0 picks a free one).

    Use as a context manager, or call ``start()`` / ``shutdown()`` yourself.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str = "127.0.0.1", port: int = 0, state: SimulatorState | None = None) -> None:
        super().__init__((host, port), _SDCPHandler)
        self.state = state if state is not None else SimulatorState()
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return str(self.server_address[0])

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def start(self) -> ProjectorSimulator:
        self._thread = threading.Thread(
            target=self.serve_forever, kwargs={"poll_interval": 0.05}, name="sdcp-simulator", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> ProjectorSimulator:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


class SDAPBroadcaster(threading.Thread):
    """Send SDAP advertisements for ``state`` to ``target`` every ``interval`` seconds."""

    def __init__(
        self,
        state: SimulatorState,
        target: tuple[str, int] = ("255.255.255.255", 53862),
        interval: float = SDAP_INTERVAL,
    ) -> None:
        super().__init__(name="sdap-broadcaster", daemon=True)
        self.state = state
        self.target = target
        self.interval = interval
        self._stop_event = threading.Event()
        self.sent = 0

    def run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while not self._stop_event.is_set():
                try:
                    sock.sendto(build_sdap_packet(self.state), self.target)
                    self.sent += 1
                except OSError as err:
                    _LOGGER.warning("SDAP send to %s failed: %s", self.target, err)
                self._stop_event.wait(self.interval)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=5)

    def __enter__(self) -> SDAPBroadcaster:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pysdcp_extended.simulator", description="Run a fake Sony projector.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on for SDCP (default: all)")
    parser.add_argument("--port", type=int, default=53484, help="SDCP TCP port (default: 53484)")
    parser.add_argument("--community", default="SONY", help="PJ Talk community (default: SONY)")
    parser.add_argument("--model", default="VPL-VW520", help="model name sent in SDAP (default: VPL-VW520)")
    parser.add_argument("--serial", type=int, default=1234567, help="serial number sent in SDAP (default: 1234567)")
    parser.add_argument("--lamp-hours", type=int, default=123, help="lamp hours to report (default: 123)")
    parser.add_argument("--sdap-port", type=int, default=53862, help="SDAP UDP port to broadcast to (default: 53862)")
    parser.add_argument("--sdap-target", default="255.255.255.255", help="SDAP destination address (default: broadcast)")
    parser.add_argument("--sdap-interval", type=float, default=SDAP_INTERVAL, help="seconds between SDAP packets (default: 30)")
    parser.add_argument("--no-sdap", action="store_true", help="do not send SDAP advertisements")
    parser.add_argument("--on", action="store_true", help="start powered on instead of in standby")
    parser.add_argument(
        "--warm-up", type=float, default=None, help="seconds from power on to POWER_ON (default: stay in START_UP)"
    )
    parser.add_argument("--cool-down", type=float, default=None, help="seconds of COOLING after power off (default: none)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    state = SimulatorState(
        community=args.community,
        product_name=args.model,
        serial_number=args.serial,
        lamp_hours=args.lamp_hours,
        power=POWER_STATUS["POWER_ON"] if args.on else POWER_STATUS["STANDBY"],
        warm_up_seconds=args.warm_up,
        cool_down_seconds=args.cool_down,
    )
    with ProjectorSimulator(args.host, args.port, state) as sim:
        _LOGGER.info("SDCP simulator listening on %s:%s (community %s)", sim.host, sim.port, state.community)
        broadcaster = None
        if not args.no_sdap:
            broadcaster = SDAPBroadcaster(state, (args.sdap_target, args.sdap_port), args.sdap_interval)
            broadcaster.start()
            _LOGGER.info("SDAP advertisements every %ss to %s:%s", args.sdap_interval, args.sdap_target, args.sdap_port)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if broadcaster is not None:
                broadcaster.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
