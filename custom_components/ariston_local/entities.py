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
class SensorSpec:
    menu: str
    name: str
    unit: str | None = None


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
    NumberSpec("6.9.0", "DHW comfort setpoint", 35, 65, 1),
]

# Mode selector. Only value 1 = Winter is label-confirmed; the rest are the
# observed settable values (see docs/05) with placeholder labels -- relabel once
# you confirm each against the app.
SELECTS = [
    SelectSpec("3", "Season mode", {
        "Off (0)": 0, "Winter": 1, "Mode 2": 2, "Mode 5": 5}),
]

# On/off toggles (NOT 6.16.0 connectivity -- disabling it can cut the gateway off)
SWITCHES = [
    SwitchSpec("6.9.5.0", "Anti-legionella"),
    SwitchSpec("6.16.6", "Internet time"),
    SwitchSpec("6.16.7", "Internet weather"),
]

# Read-only values
SENSORS = [
    SensorSpec("6.16.5", "Wi-Fi signal strength"),
    SensorSpec("6.9.5.1", "Anti-legionella frequency", "h"),
]


def all_menus():
    menus = set()
    for group in (NUMBERS, SELECTS, SWITCHES, SENSORS):
        for spec in group:
            menus.add(spec.menu)
    return sorted(menus)


def all_codes(prefix="U_"):
    return [f"{prefix}{m}" for m in all_menus()]
