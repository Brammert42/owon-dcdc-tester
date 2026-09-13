"""One measurement implementation for all workflows; invalid samples fail the run."""

import datetime
import threading
from instruments.drivers import hardware_lock
from .safety import number, check_measurement


class MeasurementError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled("Stopped by operator")


def wait(seconds, cancel=None):
    seconds = number(seconds, "Wait time", maximum=60)
    if (cancel or threading.Event()).wait(seconds):
        raise Cancelled("Stopped by operator")


def take_measurement(
    psu,
    load,
    test_mode,
    unit_serial_or_label=None,
    step_index=None,
    step_name="",
    requested_power_w=None,
    psu_set_voltage=None,
    psu_current_limit=None,
    load_set_current=None,
    samples=1,
    cancel=None,
    safety_limits=None,
    **unused,
):
    if psu is None or load is None:
        raise MeasurementError("Both instruments must be connected")
    samples = int(number(samples, "Sample count", minimum=1, maximum=100))
    values = []
    for index in range(samples):
        check_cancel(cancel)
        try:
            with hardware_lock:
                check_cancel(cancel)
                point = {}
                for key, method in [
                    ("vin", psu.measure_voltage),
                    ("iin", psu.measure_current),
                    ("vout", load.measure_voltage),
                    ("iout", load.measure_current),
                ]:
                    check_cancel(cancel)
                    point[key] = number(method(), key)
                point["pin"] = point["vin"] * point["iin"]
                point["pout"] = point["vout"] * point["iout"]
                number(point["pin"], "Input power")
                number(point["pout"], "Output power")
                if safety_limits is not None:
                    check_measurement(
                        dict(
                            point,
                            efficiency_percent=100 * point["pout"] / point["pin"]
                            if point["pin"] > 0.001
                            else 0.0,
                        ),
                        safety_limits,
                        load_active=bool(load_set_current and load_set_current > 0.01),
                    )
                values.append(point)
        except Cancelled:
            raise
        except Exception as exc:
            raise MeasurementError(
                f"Sample {index + 1}/{samples} invalid: {exc}"
            ) from exc
        if index < samples - 1:
            wait(0.1, cancel)
    avg = {key: sum(p[key] for p in values) / samples for key in values[0]}
    eff = 100 * avg["pout"] / avg["pin"] if avg["pin"] > 0.001 else 0.0
    number(eff, "Efficiency")
    return dict(
        timestamp=datetime.datetime.now().astimezone().isoformat(),
        test_mode=test_mode,
        unit_serial_or_label=unit_serial_or_label,
        step_index=step_index,
        step_name=step_name,
        requested_power_w=requested_power_w,
        psu_set_voltage=psu_set_voltage,
        input_voltage_group=psu_set_voltage,
        psu_current_limit=psu_current_limit,
        load_set_current=load_set_current,
        **avg,
        efficiency_percent=eff,
        samples=samples,
        valid=True,
        pass_fail_status=None,
        notes="",
    )
