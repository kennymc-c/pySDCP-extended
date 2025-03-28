# Defines and protocol details from here: https://www.digis.ru/upload/iblock/f5a/VPL-VW320,%20VW520_ProtocolManual.pdf

ACTIONS = {
    "GET": 0x01,
    "SET": 0x00
}

#Command

#Fire and forget, no response from the projector
COMMANDS_IR = {
    #PROJECTOR=17, PROJECTOR-E=19, PROJECTOR-EE=1B
    "MENU": 0x1729,
    "CURSOR_RIGHT": 0x1733,
    "CURSOR_LEFT": 0x1734,
    "CURSOR_UP": 0x1735,
    "CURSOR_DOWN": 0x1736,
    "CURSOR_ENTER": 0x175A,
    "LENS_SHIFT_UP": 0x1772,
    "LENS_SHIFT_DOWN": 0x1773,
    "LENS_SHIFT_LEFT": 0x1902,
    "LENS_SHIFT_RIGHT": 0x1903,
    "LENS_FOCUS_FAR": 0x1774,
    "LENS_FOCUS_NEAR": 0x1775,
    "LENS_ZOOM_LARGE": 0x1777,
    "LENS_ZOOM_SMALL": 0x1778,
}

COMMANDS = {
    "SET_POWER": 0x0130,
    "CALIBRATION_PRESET": 0x0002,
    "LAMP_CONTROL": 0x001A,
    "ADVANCED_IRIS": 0x001D,
    "MOTIONFLOW": 0x0059,
    "HDR": 0x007C,
    "INPUT_LAG_REDUCTION": 0x0099,
    "PICTURE_POSITION": 0x0066,
    "ASPECT_RATIO": 0x0020,
    "HDMI1_DYNAMIC_RANGE": 0x006E,
    "HDMI2_DYNAMIC_RANGE": 0x006F,
    "2D_3D_DISPLAY_SELECT": 0x0060,
    "3D_FORMAT": 0x0061,
    "INPUT": 0x0001,
    "PICTURE_MUTING": 0x0030,
    "MENU_POSITION": 0x00A6,
    "GET_STATUS_ERROR": 0x0101,
    "GET_STATUS_POWER": 0x0102,
    "GET_STATUS_LAMP_TIMER": 0x0113,
}

#Data

CALIBRATION_PRESETS = {
    "CINEMA_FILM_1": 0x0000,
    "CINEMA_FILM_2": 0x0001,
    "REF": 0x0002,
    "TV": 0x0003,
    "PHOTO": 0x0004,
    "GAME": 0x0005,
    "BRIGHT_CINEMA": 0x0006,
    "BRIGHT_TV": 0x0007,
    "USER": 0x0008,
}

LAMP_CONTROL= {
    "LOW": 0x0000,
    "HIGH": 0x0001,
}

ADVANCED_IRIS= {
    "OFF": 0x0000,
    "FULL": 0x0002,
    "LIMITED": 0x0003
}

MOTIONFLOW = {
    "OFF": 0x0000,
    "SMOTH_HIGH": 0x0001,
    "SMOTH_LOW": 0x0002,
    "IMPULSE": 0x0003,
    "COMBINATION": 0x0004,
    "TRUE_CINEMA": 0x0005
}

HDR = {
    "OFF": 0x0000,
    "ON": 0x0001,
    "AUTO": 0x0002,
}

INPUT_LAG_REDUCTION= {
    "OFF": 0x0000,
    "ON": 0x0001,
}

PICTURE_POSITIONS = {
    "1_85": 0x0000,
    "2_35": 0x0001,
    "CUSTOM_1": 0x0002,
    "CUSTOM_2": 0x0003,
    "CUSTOM_3": 0x0004,
    "CUSTOM_4": 0x0005,
    "CUSTOM_5": 0x0006
}

ASPECT_RATIOS = {
    "NORMAL": 0x0001,
    "V_STRETCH": 0x000B,
    "ZOOM_1_85": 0x000C,
    "ZOOM_2_35": 0x000D,
    "STRETCH": 0x000E,
    "SQUEEZE": 0x000F
}

DYNAMIC_RANGES = {
    "AUTO": 0x0000,
    "LIMITED": 0x0001,
    "FULL": 0x0002
}

TWO_D_THREE_D_SELECT = {
    "AUTO": 0x0000,
    "3D": 0x0001,
    "2D": 0x0002,
}

THREE_D_FORMATS = {
    "SIMULATED_3D": 0x0000,
    "SIDE_BY_SIDE": 0x0001,
    "OVER_UNDER": 0x0002,
}

INPUTS = {
    "HDMI1": 0x002,
    "HDMI2": 0x003,
}

PICTURE_MUTING = {
    "OFF": 0x0000,
    "ON": 0x0001,
}

MENU_POSITIONS= {
    "BOTTOM_LEFT": 0x0000,
    "CENTER": 0x0001,
}

#Response data

ERROR_STATUS = {
    "NO_ERROR": 0,
    "LAMP_ERROR": 1,
    "FAN_ERROR": 2,
    "COVER_ERROR": 4,
    "TEMP_ERROR": 8,
    "D5V_ERROR": 10,
    "POWER_ERROR": 20,
    "TEMP_WARNING": 40,
}

POWER_STATUS = {
    "STANDBY": 0,
    "START_UP": 1,
    "START_UP_LAMP": 2,
    "POWER_ON": 3,
    "COOLING": 4,
    "COOLING2": 5
}

RESPONSE_ERRORS = {
    0x101: "Item Error: Invalid Item",
    0x102: "Item Error: Invalid Item Request",
    0x103: "Item Error: Invalid Length",
    0x104: "Item Error: Invalid Data",
    0x111: "Item Error: Short Data",
    0x180: "Item Error: Not Applicable Item",
    0x201: "Community Error: Different Community",
    0x1001: "Request Error: Invalid Version",
    0x1002: "Request Error: Invalid Category",
    0x1003: "Request Error: Invalid Request",
    0x1011: "Request Error: Short Header",
    0x1012: "Request Error: Short Community",
    0x1013: "Request Error: Short Command",
    0xF001: "Comm Error: Timeout",
    0xF010: "Comm Error: Check Sum Error",
    0xF020: "Comm Error: Framing Error",
    0xF030: "Comm Error: Parity Error",
    0xF040: "Comm Error: Over Run Error",
    0xF050: "Comm Error: Other Comm Error",
    0xF0F0: "Comm Error: Unknown Response",
    0xF110: "NVRAM Error: Read Error",
    0xF120: "NVRAM Error: Write Error",
}