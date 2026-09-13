"""Validated configuration snapshots and atomic persistence."""

import copy
import json
import os
import uuid
from pathlib import Path
from .safety import number
from .thermal_check import thermal_settings
from app_paths import runtime_root, resource_path

BASE_DIR = runtime_root()
CONFIG_PATH = BASE_DIR / "config.json"


def validate_config(config):
    result = copy.deepcopy(config)
    if not isinstance(result.get('result_sounds_enabled', True), bool):
        raise ValueError('result_sounds_enabled must be boolean')
    result.setdefault('result_sounds_enabled', True)
    if not isinstance(result.get("dry_run", True), bool):
        raise ValueError("dry_run must be boolean")
    limits = result.setdefault("safety_limits", {})
    defaults = {
        "max_input_voltage": 90,
        "max_input_current": 6,
        "max_load_current": 15,
        "max_output_power": 180,
        "nominal_output_voltage": 12,
        "min_output_voltage_allowed": 10.5,
        "max_output_voltage_allowed": 14.5,
        "max_efficiency_percent": 105,
        "max_voltage_curves_per_run": 10,
    }
    for key, default in defaults.items():
        limits[key] = number(limits.get(key, default), key, minimum=0.001)
    if limits["min_output_voltage_allowed"] > limits["max_output_voltage_allowed"]:
        raise ValueError("Output voltage limits reversed")
    limits["max_voltage_curves_per_run"] = int(
        number(limits["max_voltage_curves_per_run"], "Graph traces", 1, 10)
    )
    result["settling_time_s"] = number(
        result.get("settling_time_s", 2), "Settling time", 0, 60
    )
    result["measurement_samples"] = int(
        number(result.get("measurement_samples", 3), "Samples", 1, 100)
    )
    result["cmd_retries"] = int(
        number(result.get("cmd_retries", 2), "Command attempts", 1, 3)
    )
    inst = result.setdefault("instruments", {})
    inst["timeout_s"] = number(inst.get("timeout_s", 2), "Serial timeout", 0.1, 5)
    inst["baudrate"] = int(
        number(inst.get("baudrate", 115200), "Baudrate", 1200, 1000000)
    )
    if inst.get("psu_output_state_format", "auto") not in (
        "auto",
        "boolean",
        "voltage",
    ):
        raise ValueError("Invalid PSU output state format")
    if result.get("production_test_profile"):
        profile = result["production_test_profile"]
        profile["thermal_check"] = thermal_settings(profile.get("thermal_check"))
    return result


def load_config(path=CONFIG_PATH):
    path = Path(path)
    if path == CONFIG_PATH and not path.exists() and path != resource_path('config.json'):
        # Seed bundled defaults once; application updates must not overwrite saved settings.
        with resource_path('config.json').open(encoding='utf-8') as f:
            save_config(json.load(f), path)
    with open(path, encoding='utf-8') as f:
        return validate_config(json.load(f))


def save_config(config, path=CONFIG_PATH):
    validated = validate_config(config)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding='utf-8') as f:
            json.dump(validated, f, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    return validated


def is_dry_run(c):
    return c.get("dry_run", True)


def get_safety_limits(c):
    return c["safety_limits"]


def get_steps(c):
    return c.get("steps", [])


def get_data_dir(c):
    return BASE_DIR / c.get("data_dir", "data/test_logs")


def get_log_dir(c):
    return BASE_DIR / c.get("log_dir", "data/logs")


def get_settle_time(c):
    return c["settling_time_s"]


def get_sample_count(c):
    return c["measurement_samples"]


def get_cmd_retries(c):
    return c["cmd_retries"]


def get_serial_timeout(c):
    return c["instruments"]["timeout_s"]


def get_max_curves(c):
    return c["safety_limits"]["max_voltage_curves_per_run"]


def get_serial_ports(c):
    return c.get("serial_ports", {})


def get_baudrate(c):
    return c["instruments"]["baudrate"]
