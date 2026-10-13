# Contributing

Thanks for helping out. This page covers setting up a development environment, running the
checks, and testing against the simulator or a real projector.

## Development setup

The project uses [Poetry](https://python-poetry.org/) for dependencies and packaging.
Python 3.11 or newer is required.

```shell
pipx install poetry            # or: pip install --user poetry
git clone https://github.com/kennymc-c/pySDCP-extended.git
cd pySDCP-extended
poetry install --with dev
```

`poetry install` creates a virtual environment and installs the library in editable mode
together with the development tools (pytest, pytest-cov, ruff, mypy). Prefix commands with
`poetry run`, or start a shell inside the environment with `poetry env activate`.

If you prefer the virtual environment inside the project folder (handy for editors), run
`poetry config virtualenvs.in-project true` once before `poetry install`.

## Checks

These are the same four steps the GitHub Actions workflow runs on every push and pull request
(`.github/workflows/test.yaml`), on Python 3.11, 3.12, 3.13 and 3.14.

```shell
poetry run ruff check .                     # lint
poetry run ruff format --check .            # formatting (use `ruff format .` to fix)
poetry run mypy                             # type check (strict) of the package and the tests
poetry run pytest                           # tests
```

Useful variations:

```shell
poetry run pytest -v                        # one line per test
poetry run pytest --cov                     # with a coverage report
poetry run pytest tests/test_projector.py   # one file
poetry run pytest -k timeout                # tests whose name matches
poetry run pytest --durations=10            # find slow tests
```

The whole suite runs in well under ten seconds. Every test that touches the network uses
`127.0.0.1` and an ephemeral port, so the suite needs no hardware and no network access, and
several copies can run at once.

Please run `ruff format .` before committing; the CI fails on unformatted code.

## How the tests work

The library speaks plain TCP and UDP, so the tests talk to a fake projector instead of mocking
sockets. `pysdcp_extended/simulator.py` contains:

- `ProjectorSimulator`: a threaded TCP server that answers SDCP requests the way a VPL-VW
  series projector does. GET returns the stored value, SET validates the value against the
  tables in `protocol.py` and stores it, IR commands are accepted without a reply, and bad
  requests get the matching SDCP error code (invalid item, invalid data, different community,
  and so on).
- `SimulatorState`: the simulator's memory (power, lamp hours, every setting) and the knobs
  that make it misbehave: `fail_with` (answer a command with a given error code), `reply=False`
  (never answer, to trigger timeouts), `reply_bytes` (answer with arbitrary bytes),
  `hold_seconds` (delay the answer). It also records every request in `received`.
- `SDAPBroadcaster`: a thread that sends SDAP advertisements for a state to a given address,
  used to test discovery over localhost.
- `handle_request()` / `build_response()` / `build_sdap_packet()`: the pure functions behind
  the server, usable directly in tests that do not need a socket.

`tests/conftest.py` provides fixtures that start a simulator per test (`simulator`, `state`)
and a `Projector` pointed at it (`projector`), plus `free_udp_port` / `free_tcp_port` for tests
that need a port nobody listens on.

A typical test:

```python
def test_hdmi_input(projector: Projector, state: SimulatorState) -> None:
    projector.set_HDMI_input(2)
    assert state.get("INPUT") == INPUTS["HDMI2"]
    assert projector.get_input() == "HDMI 2"


def test_timeout(projector: Projector, state: SimulatorState) -> None:
    state.reply = False
    with pytest.raises(SDCPTimeoutError):
        projector.get_power()
```

Test files:

| File | Covers |
|---|---|
| `tests/test_protocol.py` | building request frames and parsing responses and SDAP packets, no sockets |
| `tests/test_projector.py` | every public `Projector` method against the simulator, and every error path |
| `tests/test_discovery.py` | `discover_projectors()`, `find_projector()` and `get_pjinfo()` over localhost UDP |
| `tests/test_simulator.py` | the simulator's own request handling |
| `tests/test_cli.py` | the command line client |

When you add a command or a setting:

1. Add the constants to `protocol.py`. If it is a GET/SET setting with a value table, add it to
   `SETTINGS` as well; `get_setting()` / `set_setting()`, the simulator and the CLI pick it up
   from there, and `test_every_named_setting_round_trips` covers it automatically.
2. Add a helper method to `Projector` if the setting deserves one.
3. Add a test for anything that is not just a table entry.

## Trying it by hand

### Against the simulator

Start a fake projector in one terminal. By default it listens on all interfaces on TCP 53484
(the real SDCP port) and broadcasts SDAP advertisements to UDP 53862 every 30 seconds, so it
looks like a projector to anything on your machine or your network.

```shell
poetry run python -m pysdcp_extended.simulator
poetry run python -m pysdcp_extended.simulator --port 5348 --no-sdap          # another port, no SDAP
poetry run python -m pysdcp_extended.simulator --model VPL-VW665 --serial 42 --on --lamp-hours 1500
poetry run python -m pysdcp_extended.simulator --sdap-target 127.0.0.1 --sdap-interval 2   # fast discovery on localhost
```

It logs every request it receives. In another terminal, use the command line client:

```shell
poetry run python -m pysdcp_extended --ip 127.0.0.1 status
poetry run python -m pysdcp_extended --ip 127.0.0.1 power on
poetry run python -m pysdcp_extended --ip 127.0.0.1 input 2
poetry run python -m pysdcp_extended --ip 127.0.0.1 get ASPECT_RATIO
poetry run python -m pysdcp_extended --ip 127.0.0.1 set ASPECT_RATIO ZOOM_2_35
poetry run python -m pysdcp_extended --ip 127.0.0.1 ir MENU
poetry run python -m pysdcp_extended discover --timeout 5     # needs the simulator's SDAP to reach this host
poetry run python -m pysdcp_extended --ip 127.0.0.1 info
```

Or from Python:

```python
from pysdcp_extended import Projector

p = Projector("127.0.0.1")
p.get_power_status()
p.set_setting("HDR", "AUTO")
```

Point any application that uses this library (a Home Assistant integration, a remote control
integration) at the machine running the simulator to test it without a projector. To test how
the application copes with a projector that is switched off, stop the simulator; to test
timeouts, start it with `--port` on a port where a firewall drops packets, or use
`SimulatorState(reply=False)` from Python.

### Against a real projector

`scripts/hardware_check.py` runs a projector through every part of the library and writes a
Markdown report that can be pasted into a pull request or an issue. It works in stages, from
harmless to intrusive, and only the read-only stages run unless you ask for more:

| Option | What it does |
|---|---|
| (always) | listens for SDAP advertisements, reads the power status, every helper getter and every setting in `protocol.SETTINGS`, and checks the error types for a wrong community and a closed port |
| no `--ip` | finds the projector and its community from the advertisement, and also tests `Projector()` created without an IP; with several projectors it asks which one to test |
| `--write` | changes each setting, reads it back and restores the original value; needs the projector on with a picture |
| `--include-lens` | with `--write`, also changes `PICTURE_POSITION`, which moves the lens |
| `--ir` | opens, moves through and closes the on-screen menu with simulated remote keys |
| `--power` | switches the projector off and on again (or on and off), logging every power state with timestamps, and tries to power on while it cools down |
| `--soak SECONDS` | polls from two threads at once to find connection limits |
| `--redact` | hides the IP address and serial number in the report |

It asks before writes and power changes unless `--yes` is given, puts every setting back even
when a step fails, and exits with status 1 if anything failed.

Before using it on hardware, dry-run it against the simulator to see what the report looks like:

```shell
poetry run python -m pysdcp_extended.simulator --host 127.0.0.1 --on --warm-up 5 --cool-down 5 \
    --sdap-target 127.0.0.1 --sdap-interval 2 --model VPL-VW295ES
poetry run python scripts/hardware_check.py --ip 127.0.0.1 --discovery-timeout 5 --write --ir --power --soak 10
```

On the projector:

1. Enable network control on the projector and note the PJ Talk community, which is `SONY`
   unless you changed it. To switch the projector on over the network from standby, its
   standby network setting must be enabled as well (on many models, **Remote Start**).
2. Turn the projector on and wait for a picture. Most settings can only be read and changed
   while it is on.
3. Run the stages, read-only first. The first run finds the projector on its own and prints its
   IP address; use that address for the later runs so they skip the wait for an advertisement:

   ```shell
   poetry run python scripts/hardware_check.py
   poetry run python scripts/hardware_check.py --ip 10.1.2.3 --skip-discovery --write --ir
   poetry run python scripts/hardware_check.py --ip 10.1.2.3 --skip-discovery --power
   poetry run python scripts/hardware_check.py --ip 10.1.2.3 --skip-discovery --soak 600 --redact
   ```

How to read the report:

- ❌ **FAIL** is a bug: an unexpected exception type, a call that took longer than the timeout,
  an IR key that waited for a reply, or a setting that could not be restored (the report says
  which value to set back by hand).
- ⚠️ **WARN** needs a look: a value the library's tables do not know (the raw number is shown,
  so it can be added to `protocol.py`), a write the projector accepted but did not apply, or
  errors under concurrent polling.
- ℹ️ **INFO** is expected model-specific behaviour, for example a setting the model does not
  have ("Not Applicable Item") or one that only applies with a matching signal (3D, HDR).
- If discovery hears nothing, the report lists the usual causes: a different network segment
  or VLAN (SDAP is a broadcast), a firewall blocking UDP 53862, Docker or WSL2 without host
  networking, or advertising switched off on the projector. `python -m pysdcp_extended discover
  --timeout 35` is a quick way to retry discovery on its own.

If something behaves differently from the protocol manual, please open an issue with the
report, ideally made with `--redact`.

## Pull requests

- Keep each pull request to one topic.
- Add or update tests for behaviour changes, and run all four checks before pushing.
- Add an entry under `## [Unreleased]` in `CHANGELOG.md`.
- Do not bump the version in `pyproject.toml`; releases set it from the git tag.

## Releasing (maintainers)

Releases are built and published to PyPI by `.github/workflows/build+publish.yaml` when a
version tag is pushed. The tag sets the package version.

```shell
# move the Unreleased section in CHANGELOG.md under a new version heading, commit, then:
git tag 0.3.0
git push origin 0.3.0
```

The workflow refuses to publish a version that is not newer than the one already on PyPI.
