"""Battery boundary for Agent 2.

`manager.py` owns energy drain, the low/critical reserve thresholds, the
`BATTERY_LOW` announcement cadence, charger selection, and the canonical
`RecoveryAction` that sends a robot home to charge.
"""

from .manager import (
    CHARGE_TARGET_PERCENT,
    CRITICAL_BATTERY_THRESHOLD_PERCENT,
    LOW_BATTERY_THRESHOLD_PERCENT,
    BatteryDecision,
    BatteryManager,
    distance_to,
)

__all__ = [
    "CHARGE_TARGET_PERCENT",
    "CRITICAL_BATTERY_THRESHOLD_PERCENT",
    "LOW_BATTERY_THRESHOLD_PERCENT",
    "BatteryDecision",
    "BatteryManager",
    "distance_to",
]
