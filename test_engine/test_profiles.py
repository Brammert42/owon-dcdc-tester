"""Validated sweep generation. Production profiles are saved in config.json."""

import copy
from .config import load_config, save_config
from .safety import number, require_limits


def generate_sweep_steps(
    *,
    input_voltage=48,
    input_current_limit=5,
    start_power=5,
    end_power=100,
    num_steps=10,
    nominal_output_voltage=12,
    config=None,
):
    config = config or load_config()
    v = number(input_voltage, "Input voltage", 0.001)
    current = number(input_current_limit, "Input current", 0.001)
    start = number(start_power, "Start power")
    end = number(end_power, "End power")
    n = int(number(num_steps, "Step count", 1, 1000))
    nom = number(nominal_output_voltage, "Nominal output voltage", 0.001)
    if start > end:
        raise ValueError("Start power must not exceed end power")
    steps = []
    for i in range(n):
        power = start + (end - start) * i / (n - 1) if n > 1 else start
        load = power / nom
        require_limits(
            voltage=v,
            current=current,
            load_current=load,
            output_power=power,
            config=config,
        )
        steps.append(
            dict(
                name=f"Step {i + 1}",
                index=i,
                requested_power_w=power,
                load_current_a=load,
                psu_voltage=v,
                input_current_limit=current,
                notes=f"{power:.2f} W @ {nom} V nominal",
            )
        )
    return steps


def load_profile(profile_name="production_test_profile"):
    # The historical directory was empty at migration. There is one explicit profile source.
    if profile_name != "production_test_profile":
        raise ValueError("Unknown production profile")
    return copy.deepcopy(load_config().get("production_test_profile"))


def save_profile(profile, profile_name="production_test_profile"):
    if profile_name != "production_test_profile":
        raise ValueError("Unknown production profile")
    config = load_config()
    config["production_test_profile"] = profile
    save_config(config)


def init_profiles():
    pass


def list_profiles():
    return ["production_test_profile"]
