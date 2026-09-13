"""Deterministic shared converter simulator; never reports power with PSU off."""


class SimPSU:
    def __init__(self):
        self.voltage, self.current_limit, self.on = 48.0, 5.0, False
        self.load = None

    def port(self):
        return "simulation:psu"

    def identify(self):
        return "OWON,SPE15054,SIM001,1.0"

    def set_remote(self):
        pass

    def set_local(self):
        pass

    def disconnect(self):
        self.on = False

    def set_voltage(self, v):
        self.voltage = v

    def get_voltage_setting(self):
        return self.voltage

    def set_current_limit(self, i):
        self.current_limit = i

    def get_current_limit(self):
        return self.current_limit

    def output_on(self):
        self.on = True

    def output_off(self):
        self.on = False

    def safe_off(self):
        self.output_off()

    def output_status(self):
        return self.on

    def measure_voltage(self):
        return self.voltage if self.on else 0.0

    def measure_current(self):
        if not self.on or self.voltage <= 0:
            return 0.0
        power = self.load.measure_power() if self.load else 0
        # Keep the dry-run model above the production acceptance floor while
        # retaining a small input-current overhead.
        return power / 0.95 / self.voltage + 0.001

    def measure_power(self):
        return self.measure_voltage() * self.measure_current()


class SimLoad:
    def __init__(self, psu=None, nominal_voltage=12.0):
        self.psu, self.nominal_voltage, self.current, self.on = (
            psu,
            nominal_voltage,
            0.0,
            False,
        )
        if psu:
            psu.load = self

    def port(self):
        return "simulation:load"

    def identify(self):
        return "OWON,OEL1520,SIM002,1.0"

    def set_remote(self):
        pass

    def set_local(self):
        pass

    def disconnect(self):
        self.on = False

    def set_function(self, function):
        if function != "CURRent":
            raise ValueError("Only CC supported")

    def set_mode(self, mode):
        if mode != "NORM":
            raise ValueError("Only NORM supported")

    def set_cc_current(self, current):
        self.current = current

    def get_cc_current(self):
        return self.current

    def input_on(self):
        self.on = True

    def input_off(self):
        self.on = False

    def input_status(self):
        return self.on

    def safe_off(self):
        self.input_off()
        self.current = 0

    def measure_voltage(self):
        if not self.psu or not self.psu.on:
            return 0.0
        return max(0, self.nominal_voltage - self.measure_current() * 0.015)

    def measure_current(self):
        return self.current if self.on and self.psu and self.psu.on else 0.0

    def measure_power(self):
        return self.measure_voltage() * self.measure_current()
