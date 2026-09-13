"""Shared setpoint, profile and measured-value validation."""

import math

PRODUCTION_EFFICIENCY_FLOOR = 90.0


class SafetyError(ValueError):
    pass


def number(value, name, minimum=0, maximum=None):
    if isinstance(value, bool):
        raise SafetyError(f"{name} must be a number")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise SafetyError(f"{name} must be a number") from exc
    if (
        not math.isfinite(value)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        raise SafetyError(
            f"{name} must be finite and between {minimum} and {maximum if maximum is not None else 'infinity'}"
        )
    return value


def validate_limits(
    voltage=None, current=None, load_current=None, output_power=None, config=None
):
    limits = (config or {}).get("safety_limits", config or {})
    errors = []
    for value, name, key, default in (
        (voltage, "Input voltage", "max_input_voltage", 90),
        (current, "Input current", "max_input_current", 6),
        (load_current, "Load current", "max_load_current", 15),
        (output_power, "Output power", "max_output_power", 180),
    ):
        if value is not None:
            try:
                number(value, name, maximum=number(limits.get(key, default), key))
            except SafetyError as exc:
                errors.append(str(exc))
    return errors


def require_limits(**kwargs):
    errors = validate_limits(**kwargs)
    if errors:
        raise SafetyError("; ".join(errors))


def check_voltage_collapse(vout, min_vout, load_active):
    if not math.isfinite(vout):
        return True, "Invalid output voltage"
    if load_active and vout < min_vout:
        return True, f"Output voltage collapsed: {vout:.4f} V < {min_vout} V"
    return False, None


def check_production_step(vout, efficiency, step):
    if not math.isfinite(vout) or not math.isfinite(efficiency):
        return False, "Invalid voltage or efficiency measurement"
    for value, key, low, label in (
        (vout, "min_vout", True, "Vout"),
        (vout, "max_vout", False, "Vout"),
        (efficiency, "min_efficiency_percent", True, "Efficiency"),
    ):
        limit = step.get(key)
        if key == "min_efficiency_percent" and limit is not None:
            limit = max(float(limit), PRODUCTION_EFFICIENCY_FLOOR)
        if limit is not None and (
            (value <= limit)
            if key == "min_efficiency_percent" and low
            else (value < limit)
            if low
            else (value > limit)
        ):
            return False, f"{label} {value:.4f} violates {key}={limit}"
    return True, None


def check_measurement(point, limits, load_active=True):
    require_limits(
        voltage=point["vin"],
        current=point["iin"],
        load_current=point["iout"],
        output_power=point["pout"],
        config=limits,
    )
    number(
        point["vout"],
        "Output voltage",
        maximum=limits.get("max_output_voltage_allowed", 14.5),
    )
    collapsed, reason = check_voltage_collapse(
        point["vout"], limits.get("min_output_voltage_allowed", 10.5), load_active
    )
    if collapsed:
        raise SafetyError(reason)
    if load_active and point["pin"] <= 0.001:
        raise SafetyError(
            "Input power is below the measurement floor with load enabled"
        )
    number(
        point["efficiency_percent"],
        "Efficiency",
        maximum=limits.get("max_efficiency_percent", 105),
    )
