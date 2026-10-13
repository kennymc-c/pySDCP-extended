# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Typed exceptions: `SDCPError` (base), `SDCPConnectionError`, `SDCPTimeoutError`, `SDCPProtocolError`, `SDCPCommandError` (with `error_code` / `error_message`) and `SDCPDiscoveryError`. Timeouts used to raise a bare `Exception`.
- `send_command()` as the public way to send any command from `protocol.py` (`_send_command()` still works)
- `get_setting()` / `set_setting()` for every entry of the new `protocol.SETTINGS` table, plus `get_aspect_ratio()`, `set_aspect_ratio()`, `get_picture_position()`, `set_picture_position()` and `get_screen()`
- `send_ir_command()` for the simulated remote control keys in `COMMANDS_IR`
- `get_power_status()` returning the `POWER_STATUS` name, and `get_lamp_timer()` returning the lamp hours as a number
- `discover_projectors()` to collect every projector advertising via SDAP, with an optional `limit`; `find_projector()` now returns `True` on success
- `Projector(tcp_timeout=..., udp_timeout=...)` constructor arguments
- Type hints throughout and a `py.typed` marker
- A projector simulator (`python -m pysdcp_extended.simulator`) and a command line client (`python -m pysdcp_extended`)
- Test suite (pytest), ruff and mypy configuration, and a GitHub Actions workflow running them on Python 3.11 to 3.14
- `scripts/hardware_check.py` to exercise a real projector stage by stage and write a Markdown report
- VPL-VW295ES in the list of supported projectors

### Changed

- Invalid arguments to `set_screen()` raise `ValueError` instead of a bare `Exception`
- `get_pjinfo()` raises `SDCPDiscoveryError` instead of a bare `Exception` when nothing is heard, and only accepts advertisements from the projector's own IP when one is set
- The SDAP listening socket is opened with `SO_REUSEADDR` so several listeners can share the port

### Fixed

- Sockets are now always closed, also when a command fails
- A projector that closes the connection without answering, or answers with a truncated frame, raises `SDCPProtocolError` instead of `IndexError` / `struct.error`
- Simulated IR commands from the `PROJECTOR-EE` (`0x1B..`) code set were waiting for a reply that never comes

## [0.2.1] - 2026-05-23

### Added

- Added functions to get/set calibration presets and HDMI dynamic range ([#2](https://github.com/kennymc-c/pySDCP-extended/pull/2))

## [0.2.0] - 2025-03-23

### Added

- Added advanced iris commands (Off, Full, Limited) and additional picture positions CUSTOM_4 & CUSTOM_5 (only supported by certain models)

## [0.1.0] - 2024-12-11

This is the first release published on pyPi.org
