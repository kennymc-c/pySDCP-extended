# pySDCP-extended

[![PyPi](https://img.shields.io/pypi/v/pysdcp-extended.svg)](https://pypi.org/project/pysdcp-extended)

Sony SDCP / PJ Talk projector control.

Python **3** library to query and control Sony Projectors using SDCP (PJ Talk) protocol over IP.

## Features

* Auto discover projector using SDAP (Simple Display Advertisement Protocol)
* Query model name and serial number
* Query and change power, input and picture muting status
* Query lamp hours
* Change input to HDMI 1 and HDMI 2
* Set aspect ratio/zoom and calibration presets
* Show response error message from the projector

### More features

The SDCP protocol allow to control practically everything in projector, i.e. resolution, brightness, 3d format...
If you need to use more commands, just add to _protocol.py_, and send with _my_projector._send_command__
Please note that commands in `COMMANDS_IR` work as fire and forget and you only get a response if there is a timeout.

## Protocol Documentation

* [Link](https://www.digis.ru/upload/iblock/f5a/VPL-VW320,%20VW520_ProtocolManual.pdf)
* [Link](https://docs.sony.com/release/VW100_protocol.pdf)

## Supported Projectors

Supported Sony projectors should include:

* VPL-HW65ES
* VPL-VW100
* VPL-VW260
* VPL-VW270
* VPL-VW285
* VPL-VW315
* VPL-VW320
* VPL-VW328
* VPL-VW365
* VPL-VW515
* VPL-VW520
* VPL-VW528
* VPL-VW665

## Installation

```pip install pysdcp-extended```

## Examples

Sending any command will initiate auto discovery of the projector if none is known and will carry on the command. So just go for it and maybe you get lucky

```python
import pysdcp_extended as pysdcp

my_projector = pysdcp.Projector()

my_projector.get_power()
my_projector.set_power(True)
```

Skip discovery to save time or if you know the IP of the projector

```python
my_known_projector = pysdcp.Projector('10.1.2.3')
my_known_projector.set_HDMI_input(2)
```

## Credits

This plugin is an extended fork of [pySDCP](https://github.com/Galala7/pySDCP) by [Galala7](https://github.com/Galala7) which is based on [sony-sdcp-com](https://github.com/vokkim/sony-sdcp-com) NodeJS library by [vokkim](https://github.com/vokkim).

## See also

* [homebridge-sony-sdcp](https://github.com/Galala7/homebridge-sony-sdcp) - Homebridge plugin to control Sony Projectors (based on Galala7/pySDCP)
* [ucr2-integration-sonySDCP](https://github.com/kennymc-c/ucr2-integration-sonySDCP) - SDCP integration for Unfolded Circle Remote devices
