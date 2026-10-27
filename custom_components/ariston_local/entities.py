"""Entity catalog: which Menu/Par codes become which Home Assistant entities.

Data-driven so the platforms stay tiny and the map is the single source of truth.
Each entry references a menu number; the code sent is U_<menu>. Only parameters
confirmed on-device (see docs/05) are exposed. Extend as you map more.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NumberSpec:
    menu: str
    name: str
    min: float
    max: float
    step: float
    unit: str = "°C"


@dataclass(frozen=True)
class SelectSpec:
    menu: str
    name: str
    # option label -> raw value; raw value -> label is derived
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SwitchSpec:
    menu: str
    name: str


@dataclass(frozen=True)
class ClimateSpec:
    zone: int
    name: str
    temp_menu: str       # T_ live room temperature
    setpoint_menu: str   # T_ live active setpoint (reflects schedule or override)
    mode_menu: str       # U_ zone mode (2 = Manuel, 3 = Chauffage programmé)
    min_temp: float = 10.0
    max_temp: float = 30.0
    step: float = 0.5


@dataclass(frozen=True)
class SensorSpec:
    menu: str
    name: str
    unit: str | None = None
    prefix: str = "U_"  # T_ for technical/live reads, U_ for user values
    device_class: str | None = None


@dataclass(frozen=True)
class BinarySpec:
    menu: str
    name: str
    prefix: str = "T_"
    device_class: str | None = None


# Writable setpoints (°C, x10 scaling handled via menu.py)
NUMBERS = [
    NumberSpec("6.3.1.0.0", "Zone 1 comfort setpoint", 10, 30, 0.5),
    NumberSpec("6.3.1.0.1", "Zone 1 eco setpoint", 10, 30, 0.5),
    NumberSpec("6.3.1.1.0", "Zone 2 comfort setpoint", 10, 30, 0.5),
    NumberSpec("6.3.1.1.1", "Zone 2 eco setpoint", 10, 30, 0.5),
    NumberSpec("6.3.1.2.0", "Zone 3 comfort setpoint", 10, 30, 0.5),
    NumberSpec("6.3.1.2.1", "Zone 3 eco setpoint", 10, 30, 0.5),
    NumberSpec("6.3.0.0", "Zone 1 flow setpoint", 20, 80, 1),
    NumberSpec("6.3.0.2", "Zone 2 flow setpoint", 20, 80, 1),
    NumberSpec("6.3.0.4", "Zone 3 flow setpoint", 20, 80, 1),
    NumberSpec("6.9.0", "DHW comfort setpoint", 40, 65, 1),  # device bounds (SystemState)
]

# Mode selector. All labels confirmed byte-for-byte from a capture (see docs/05):
# the app wrote 0=Été, 1=Hiver, 2=chauffage seul, 5=arrêt.
SELECTS = [
    SelectSpec("3", "Season mode", {
        "Summer": 0, "Winter": 1, "Heating only": 2, "Off": 5}),
]

# On/off toggles (NOT 6.16.0 connectivity -- disabling it can cut the gateway off)
SWITCHES = [
    SwitchSpec("6.9.5.0", "Anti-legionella"),
    SwitchSpec("6.16.6", "Internet time"),
    SwitchSpec("6.16.7", "Internet weather"),
]

# Read-only values. The T_ (technical) reads are live measurements confirmed from
# a capture (see docs/05): T_4.3.0 drifts ~20°C (room temp), T_4.3.1 tracked the
# 18° override, T_8.4.0 moves 40-42°C (boiler flow).
SENSORS = [
    SensorSpec("6.16.5", "Wi-Fi signal strength"),
    SensorSpec("6.9.5.1", "Anti-legionella frequency", "h"),
    SensorSpec("4.3.0", "Zone 1 temperature", "°C", prefix="T_",
               device_class="temperature"),
    SensorSpec("4.3.1", "Zone 1 active setpoint", "°C", prefix="T_",
               device_class="temperature"),
    SensorSpec("8.4.0", "Boiler flow temperature", "°C", prefix="T_",
               device_class="temperature"),
    SensorSpec("19.1.1", "Wi-Fi link quality", prefix="T_"),
]

# Read-only on/off status (from the T_ technical channel), via the main poll.
BINARY_SENSORS = [
    BinarySpec("2.1.0", "Burner", prefix="T_", device_class="running"),
]


# Per-zone thermostats. Only zone 1 is capture-confirmed: T_4.3.0 drifts ~20°C
# (room temp), T_4.3.1 tracked the 18° override (active setpoint), U_0.0 is the
# zone mode. Zones 2/3 await a capture of their T_ codes before being added here.
CLIMATES = [
    ClimateSpec(1, "Zone 1", "4.3.0", "4.3.1", "0.0"),
]


def all_menus():
    menus = set()
    for group in (NUMBERS, SELECTS, SWITCHES, SENSORS, BINARY_SENSORS):
        for spec in group:
            menus.add(spec.menu)
    for c in CLIMATES:
        menus.update((c.temp_menu, c.setpoint_menu, c.mode_menu))
    return sorted(menus)


def all_codes():
    """Every device code the coordinator must read, with the right prefix:
    U_ for writable/user params, each sensor's own prefix (T_ for live reads)."""
    codes = {f"U_{spec.menu}" for group in (NUMBERS, SELECTS, SWITCHES)
             for spec in group}
    codes |= {f"{spec.prefix}{spec.menu}" for spec in SENSORS}
    codes |= {f"{spec.prefix}{spec.menu}" for spec in BINARY_SENSORS}
    for c in CLIMATES:
        codes |= {f"T_{c.temp_menu}", f"T_{c.setpoint_menu}", f"U_{c.mode_menu}"}
    return sorted(codes)
