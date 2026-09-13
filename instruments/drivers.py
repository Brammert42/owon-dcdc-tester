"""Single driver API used by manual, sweep and production workflows."""

import functools
import math
import threading
import time
from instruments.serial_resource import SerialResource

hardware_lock = threading.RLock()


class DriverError(RuntimeError):
    pass


class TransactionError(DriverError):
    pass


class ConnectionError(DriverError):
    pass


def locked(method):
    @functools.wraps(method)
    def wrapped(*args, **kwargs):
        with hardware_lock:
            return method(*args, **kwargs)

    return wrapped


class InstrumentDriver:
    def __init__(self, resource: SerialResource, name="", cmd_retries=2, **unused):
        self._resource, self.name = resource, name
        self._cmd_retries = max(1, min(int(cmd_retries), 3))

    @locked
    def connect(self):
        self._resource.open()

    @locked
    def disconnect(self):
        self._resource.close()

    def port(self):
        return self._resource._port

    def is_connected(self):
        return self._resource.is_open

    @locked
    def write(self, cmd):
        # Serial exceptions are terminal: never replay an ON command after a disconnect.
        try:
            self._resource.write(cmd)
        except Exception as exc:
            raise ConnectionError(f"{self.name}: {cmd}: {exc}") from exc

    @locked
    def query(self, cmd):
        for attempt in range(self._cmd_retries):
            try:
                value = self._resource.query(cmd)
            except Exception as exc:
                raise ConnectionError(f"{self.name}: {cmd}: {exc}") from exc
            if value.strip():
                return value.strip()
            if attempt + 1 < self._cmd_retries:
                time.sleep(0.1)
        raise DriverError(f"{self.name}: empty response to {cmd}")

    def identify(self):
        return self.query("*IDN?")

    def idn(self):
        return self.identify()

    def set_remote(self):
        self.write("SYSTem:REMote")

    def set_local(self):
        self.write("SYSTem:LOCal")

    def health_check(self):
        return bool(self.identify())

    def _number(self, cmd):
        try:
            value = float(self.query(cmd))
            if not math.isfinite(value) or value < 0:
                raise ValueError("nonfinite or negative value")
            return value
        except ValueError as exc:
            raise DriverError(f"{self.name}: invalid response to {cmd}: {exc}") from exc

    @locked
    def _verify(self, command, query, check, delay=0.1):
        for _ in range(self._cmd_retries):
            self.write(command)
            time.sleep(delay)
            response = self.query(query)
            if check(response):
                return
        raise TransactionError(
            f"{self.name}: verification failed: {command}, read {response!r}"
        )

    def measure_voltage(self):
        return self._number("MEASure:VOLTage?")

    def measure_current(self):
        return self._number("MEASure:CURRent?")

    def measure_power(self):
        return self._number("MEASure:POWer?")


class OwonPSU(InstrumentDriver):
    def __init__(self, resource, output_state_format="auto", **kwargs):
        super().__init__(resource, name="PSU", **kwargs)
        self.output_state_format = output_state_format

    def _state(self, response):
        text = response.strip().upper()
        if text in ("ON", "OFF"):
            return text == "ON"
        value = float(text)
        if not math.isfinite(value) or value < 0:
            raise DriverError("Invalid PSU output state")
        if self.output_state_format == "boolean":
            if value not in (0, 1):
                raise DriverError("Unexpected PSU boolean output state")
            return bool(value)
        # Preserve the deployed SPE voltage-style OUTPut? behavior. Configurable for firmware.
        return value > 0.5

    def output_status(self):
        return self._state(self.query("OUTPut?"))

    def output_on(self):
        self._verify("OUTPut ON", "OUTPut?", self._state, delay=0.5)

    def output_off(self):
        self._verify("OUTPut OFF", "OUTPut?", lambda r: not self._state(r), delay=0.2)

    def safe_off(self):
        self.output_off()

    def set_voltage(self, voltage):
        self.write(f"VOLTage {voltage}")

    def get_voltage_setting(self):
        return self._number("VOLTage?")

    @locked
    def set_current_limit(self, current):
        self.write(f"CURRent {current}")
        time.sleep(0.1)
        self.write(f"CURRent:LIMit {current}")

    def get_current_limit(self):
        return self._number("CURRent?")


class OwonLoad(InstrumentDriver):
    def __init__(self, resource, **kwargs):
        super().__init__(resource, name="Load", **kwargs)

    def set_function(self, function):
        if function != "CURRent":
            raise DriverError("Only CC load mode is supported")
        self._verify(
            "FUNCtion CURRent",
            "FUNCtion?",
            lambda r: r.upper() in ("CURR", "CURRENT", "CC"),
        )

    def get_function(self):
        return self.query("FUNCtion?")

    def set_mode(self, mode):
        if mode != "NORM":
            raise DriverError("Only NORM load mode is supported")
        self._verify(
            "MODE NORM", "MODE?", lambda r: r.upper() in ("0", "NORM", "NORMAL")
        )

    def get_mode(self):
        return self.query("MODE?")

    def _state(self, response):
        text = response.strip().upper()
        if text not in ("0", "1", "ON", "OFF"):
            raise DriverError(f"Invalid load state: {response!r}")
        return text in ("1", "ON")

    def input_status(self):
        return self._state(self.query("INPut?"))

    def input_on(self):
        self._verify("INPut ON", "INPut?", self._state, delay=0.3)

    def input_off(self):
        self._verify("INPut OFF", "INPut?", lambda r: not self._state(r), delay=0.2)

    def set_cc_current(self, current):
        def matches(response):
            try:
                return (
                    math.isfinite(float(response))
                    and abs(float(response) - current) < 0.1
                )
            except ValueError:
                return False

        self._verify(f"CURRent {current}", "CURRent?", matches, delay=0.15)

    def get_cc_current(self):
        return self._number("CURRent?")

    def safe_off(self):
        self.input_off()  # disable first; report failure, never claim success on exception
        self.set_cc_current(0)
