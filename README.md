# pySDCP-extended

<!---[![PyPi](https://img.shields.io/pypi/v/pysdcp-extended.svg)](https://pypi.org/project/pysdcp-extended)--->

Extended Sony SDCP / PJ Talk projector control.

Python **3** library to query and control Sony Projectors using SDCP (PJ Talk) protocol over IP.

## Features

* Auto discover projectors using SDAP (Simple Display Advertisement Protocol)
* Set a custom PJ Talk community & UDP advertisement SDAP port and TCP SDCP port
* Typed exceptions, with the response error message from the projector
* Fully type annotated (ships `py.typed`)
* Get/Set
  * Power
  * Input (HDMI 1 + 2)
  * Calibration preset
  * Picture muting
  * HDMI dynamic range
  * Aspect ratio/zoom and picture position
  * Any named setting from `protocol.SETTINGS` (`get_setting()` / `set_setting()`)
  * Any raw command from `protocol.py` (`send_command()`)
* Get
  * Lamp hours
  * Power status (standby, start up, on, cooling)
  * Model name
  * Serial number
* Send simulated remote control keys (`send_ir_command()`)
* A projector simulator for tests and development (`python -m pysdcp_extended.simulator`)
* A command line client (`python -m pysdcp_extended`)

## Protocol Documentation

* [https://www.digis.ru/upload/iblock/f5a/VPL-VW320,%20VW520_ProtocolManual.pdf](https://www.digis.ru/upload/iblock/f5a/VPL-VW320,%20VW520_ProtocolManual.pdf)
* [https://docs.sony.com/release/VW100_protocol.pdf](https://docs.sony.com/release/VW100_protocol.pdf)

## Supported Projectors

Supported Sony projectors should include:

* VPL-HW65ES
* VPL-VW100
* VPL-VW260
* VPL-VW270
* VPL-VW285
* VPL-VW295ES
* VPL-VW315
* VPL-VW320
* VPL-VW328
* VPL-VW365
* VPL-VW515
* VPL-VW520
* VPL-VW528
* VPL-VW665
* VPL-XW6100

## Installation

```pip install pysdcp-extended```

## Examples

Sending any command will initiate auto discovery of the projector if none is known and will carry on the command. So just go for it and maybe you get lucky

```python
import pysdcp_extended

my_projector = pysdcp_extended.Projector()

my_projector.get_power()
my_projector.set_power(True)
```

Skip discovery to save time or if you know the IP of the projector

```python
my_known_projector = pysdcp_extended.Projector("10.1.2.3")
my_known_projector.set_HDMI_input(2)
```

You can also set a custom PJ Talk community, tcp/udp ports and timeouts. By default "SONY" will be used as the community, 53862 as udp port for SDAP advertisement, 53484 as tcp port for SDCP and 2 seconds as the TCP timeout

```python
my_known_projector = pysdcp_extended.Projector(
    ip="10.1.2.3", community="THEATER", udp_port=53860, tcp_port=53480, tcp_timeout=5
)
```

### Discovery

Projectors announce themselves on UDP port 53862 every 30 seconds. `discover_projectors()` listens for those announcements and returns everything it heard, de-duplicated on serial number. Listen for a full interval to be sure to catch every projector, or pass `limit` to stop at the first one

```python
from pysdcp_extended import discover_projectors

for found in discover_projectors(timeout=31):
    print(found.ip, found.product_name, found.serial_number)

first = discover_projectors(timeout=31, limit=1)
```

`Projector.get_pjinfo()` returns the model, serial number and IP of one projector the same way.

### Named settings

Every setting in `protocol.SETTINGS` can be read and written by name. The value names are the keys of the matching table in `protocol.py`

```python
from pysdcp_extended.protocol import SETTINGS

print(list(SETTINGS))                      # ASPECT_RATIO, PICTURE_POSITION, HDR, MOTIONFLOW, ...
print(list(SETTINGS["ASPECT_RATIO"]))      # NORMAL, V_STRETCH, ZOOM_1_85, ...

my_projector.set_setting("ASPECT_RATIO", "ZOOM_2_35")
my_projector.get_setting("ASPECT_RATIO")   # "ZOOM_2_35"
my_projector.get_setting("HDR")            # "AUTO", or the raw number if the library does not know the value
```

### Remote control keys

Commands in `COMMANDS_IR` simulate a key press on the remote. The projector does not answer them, so the call returns as soon as the command was sent

```python
my_projector.send_ir_command("MENU")
my_projector.send_ir_command("CURSOR_DOWN")
```

### Raw commands from protocol.py

Anything in `protocol.py` that has no helper yet can be sent with `send_command()`. If you need a command that is not in `protocol.py`, add it there and send it the same way

```python
from pysdcp_extended.protocol import ACTIONS, COMMANDS

my_projector.send_command(action=ACTIONS["GET"], command=COMMANDS["GET_STATUS_ERROR"])
```

### Error handling

All errors raised by the library derive from `SDCPError`

| Exception | Also a | Raised when |
|---|---|---|
| `SDCPConnectionError` | `ConnectionError` | the projector refused or dropped the connection, or a UDP port could not be bound |
| `SDCPTimeoutError` | `SDCPConnectionError`, `TimeoutError` | the projector did not answer within the TCP timeout |
| `SDCPProtocolError` | | the reply could not be parsed |
| `SDCPCommandError` | | the projector rejected the command; `.error_code` and `.error_message` carry the reason |
| `SDCPDiscoveryError` | | no projector was found via SDAP |

Invalid arguments (an unknown setting or value, an HDMI number other than 1 or 2) raise `ValueError`.

```python
from pysdcp_extended import Projector, SDCPCommandError, SDCPConnectionError, SDCPError

try:
    Projector("10.1.2.3").set_setting("HDR", "AUTO")
except SDCPCommandError as err:
    print("the projector said no:", err.error_message)
except SDCPConnectionError:
    print("projector is off the network")
except SDCPError as err:
    print("something else went wrong:", err)
```

## Command line

```shell
python -m pysdcp_extended discover --timeout 35
python -m pysdcp_extended --ip 10.1.2.3 status
python -m pysdcp_extended --ip 10.1.2.3 power on
python -m pysdcp_extended --ip 10.1.2.3 get ASPECT_RATIO
python -m pysdcp_extended --ip 10.1.2.3 set ASPECT_RATIO ZOOM_2_35
python -m pysdcp_extended --ip 10.1.2.3 ir MENU
```

## Simulator

`python -m pysdcp_extended.simulator` runs a fake projector that answers SDCP on port 53484 and sends SDAP advertisements. Use it to try the library or an application built on it without hardware. See [CONTRIBUTING.md](CONTRIBUTING.md) for details and for using it in tests.

## Credits

This plugin is an extended fork of [pySDCP](https://github.com/Galala7/pySDCP) by [Galala7](https://github.com/Galala7) which is based on [sony-sdcp-com](https://github.com/vokkim/sony-sdcp-com) NodeJS library by [vokkim](https://github.com/vokkim).

## See also

* [homebridge-sony-sdcp](https://github.com/Galala7/homebridge-sony-sdcp) - Homebridge plugin to control Sony Projectors (based on Galala7/pySDCP)
* [ucr2-integration-sonySDCP](https://github.com/kennymc-c/ucr2-integration-sonySDCP) - SDCP integration for Unfolded Circle Remote devices
