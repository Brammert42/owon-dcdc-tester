"""Settings for the operator-read thermal production check."""

from .safety import number, SafetyError

THERMAL_INPUT_TIMEOUT_S = 60.0


def thermal_settings(settings=None):
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise SafetyError("Thermal check settings must be an object")
    enabled = settings.get("enabled", False)
    if not isinstance(enabled, bool):
        raise SafetyError("Thermal check enabled must be boolean")
    return {
        "enabled": enabled,
        "max_temp_c": number(settings.get("max_temp_c", 80), "Thermal maximum °C", -20, 550),
        "warmup_s": number(settings.get("warmup_s", 30), "Thermal warm-up seconds", 0, 300),
    }
