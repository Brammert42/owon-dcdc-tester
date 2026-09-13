"""Synchronous, cancellable sequence executor owned by TestEngine's worker.

A failure escapes the step loop. The engine always shuts down both instruments
and finalizes storage before publishing a terminal result.
"""

from dataclasses import dataclass
from instruments.drivers import hardware_lock


@dataclass
class SweepStep:
    index: int
    name: str
    requested_power_w: float
    load_current_a: float
    psu_voltage: float
    psu_current_limit: float
    wait_s: float = 3.0
    notes: str = ""

    @classmethod
    def from_dict(cls, d, index):
        return cls(
            index,
            d.get("name", f"Step {index + 1}"),
            float(d["requested_power_w"]),
            float(d["load_current_a"]),
            float(d["psu_voltage"]),
            float(d["input_current_limit"]),
            float(d.get("wait_s", 3)),
            d.get("notes", ""),
        )


class SweepRunner:
    def __init__(
        self, psu, load, cancel, emit_state, sample_count=3, safety_limits=None
    ):
        self.psu, self.load = psu, load
        self.cancel = cancel
        self.emit_state = emit_state
        self.sample_count = sample_count
        self.safety_limits = safety_limits

    def action(self, state, operation):
        from test_engine.measurement import check_cancel

        self.emit_state(state)
        with hardware_lock:
            check_cancel(self.cancel)
            operation()
            check_cancel(self.cancel)

    def prepare(self, step, previous=None):
        """Apply a point while keeping an active load across compatible steps.

        A PSU input change is a protected boundary: disable the load and PSU,
        reconfigure, then enable them again. With the same input settings, the
        PSU and an already-active load stay on while only the CC setpoint changes.
        """
        same_input = (
            previous is not None
            and previous.psu_voltage == step.psu_voltage
            and previous.psu_current_limit == step.psu_current_limit
        )
        previous_load_active = previous is not None and previous.load_current_a > 0
        if previous is None:
            self.action("LOAD_OFF", self.load.input_off)
            self.action("PSU_OFF", self.psu.output_off)
        elif not same_input:
            if previous_load_active:
                self.action("LOAD_OFF", self.load.input_off)
            self.action("PSU_OFF", self.psu.output_off)

        if not same_input:
            self.action("CONFIGURE_PSU", lambda: self.psu.set_voltage(step.psu_voltage))
            self.action(
                "CONFIGURE_PSU",
                lambda: self.psu.set_current_limit(step.psu_current_limit),
            )
            self.action("CONFIGURE_LOAD", lambda: self.load.set_function("CURRent"))
            self.action("CONFIGURE_LOAD", lambda: self.load.set_mode("NORM"))
            self.action("PSU_ON", self.psu.output_on)
            self.action(
                "CONFIGURE_PSU",
                lambda: self.psu.set_current_limit(step.psu_current_limit),
            )

        self.action(
            "CONFIGURE_LOAD", lambda: self.load.set_cc_current(step.load_current_a)
        )
        if step.load_current_a > 0 and not previous_load_active:
            self.action("LOAD_ON", self.load.input_on)
        elif step.load_current_a <= 0 and previous_load_active:
            self.action("LOAD_OFF", self.load.input_off)

    def measure(self, step, mode, label=None):
        from test_engine.measurement import wait, take_measurement

        self.emit_state("SETTLING")
        wait(step.wait_s, self.cancel)
        self.emit_state("MEASURING")
        return take_measurement(
            self.psu,
            self.load,
            test_mode=mode,
            unit_serial_or_label=label,
            step_index=step.index,
            step_name=step.name,
            requested_power_w=step.requested_power_w,
            psu_set_voltage=step.psu_voltage,
            psu_current_limit=step.psu_current_limit,
            load_set_current=step.load_current_a,
            samples=self.sample_count,
            cancel=self.cancel,
            safety_limits=self.safety_limits,
        )
