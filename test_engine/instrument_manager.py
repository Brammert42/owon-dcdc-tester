"""Cached UI state. This object never performs serial I/O."""

import copy
import threading


class InstrumentManager:
    def __init__(self, dry_run=True, config=None):
        self.psu = self.load = None
        self.connected = False
        self.status = "disconnected"
        self.dry_run = dry_run
        self._config = config or {}
        self.manual_points = []
        self.sweep_points = []
        self.prod_points = []
        self.manual_run_id = self.sweep_run_id = self.prod_run_id = None
        self.csv_path = None
        self.manual_psu_set_voltage = None
        self.manual_psu_current_limit = None
        self._sweep_state = self._prod_state = "idle"
        self._lock = threading.RLock()
        self.readings = None
        self.psu_on = self.load_on = None
        self.identity = {}
        self.fault = ""
        self.busy = ""

    def is_connected(self):
        return self.connected

    @property
    def safety_limits(self):
        return self._config["safety_limits"]

    @property
    def settle_time(self):
        return self._config["settling_time_s"]

    def get_status_dict(self):
        with self._lock:
            p = copy.deepcopy(self.readings or {})
            return dict(
                status=self.status,
                connected=self.connected,
                dry_run=self.dry_run,
                psu_connected=self.connected,
                load_connected=self.connected,
                psu_output_on=self.psu_on,
                load_input_on=self.load_on,
                psu={k: p.get(k) for k in ("vin", "iin", "pin")},
                load={k: p.get(k) for k in ("vout", "iout", "pout")},
                efficiency=p.get("efficiency_percent"),
                measurement_timestamp=p.get("timestamp"),
                sweep_state=self._sweep_state,
                prod_state=self._prod_state,
                manual_count=len(self.manual_points),
                sweep_count=len(self.sweep_points),
                prod_count=len(self.prod_points),
                manual_psu_set_voltage=self.manual_psu_set_voltage,
                fault=self.fault,
                busy=self.busy,
                instruments=copy.deepcopy(self.identity),
            )
