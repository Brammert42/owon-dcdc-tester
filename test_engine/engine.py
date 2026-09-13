"""Exclusive, cancellable hardware orchestration shared by desktop and web.

The operation lease covers a whole manual action or test. Instrument I/O also
uses one lock. Stop sets cancellation before waiting for I/O. Results become
terminal only after shutdown has been attempted and its outcome is known.
"""

import copy
import logging
import os
import threading
import time
from pathlib import Path
from . import config as cfg
from .database import Store, timestamp
from .file_lock import lock_exclusive, hardware_lock_path
from .thermal_check import thermal_settings, THERMAL_INPUT_TIMEOUT_S
from .instrument_manager import InstrumentManager
from .run_data_manager import RunDataManager
from .transaction_log import TransactionLog
from .measurement import Cancelled, check_cancel, take_measurement
from .safety import (
    SafetyError,
    number,
    require_limits,
    check_measurement,
    check_production_step,
)
from instruments.drivers import OwonPSU, OwonLoad, hardware_lock
from instruments.serial_resource import SerialResource
from instruments.sweep_state_machine import SweepRunner, SweepStep
from instruments.simulation import SimPSU, SimLoad
from instruments import discovery

logger = logging.getLogger(__name__)


class BusyError(RuntimeError):
    pass


class ShutdownError(RuntimeError):
    pass


class TestEngine:
    def __init__(self, dry_run=None, config=None, storage_root=None):
        self._config = cfg.validate_config(
            config if config is not None else cfg.load_config()
        )
        self.dry_run = cfg.is_dry_run(self._config) if dry_run is None else dry_run
        if storage_root is None and self.dry_run:
            storage_root = cfg.BASE_DIR / "data/simulation"
        root = Path(storage_root) if storage_root else cfg.BASE_DIR / "data"
        csv_dir = root / "test_logs" if storage_root else cfg.get_data_dir(self._config)
        log_dir = root / "logs" if storage_root else cfg.get_log_dir(self._config)
        self.store = Store(root / "test_results.sqlite", csv_dir)
        try:
            self._tlog = TransactionLog(log_dir)
        except Exception:
            self.store.close()
            raise
        self.manager = InstrumentManager(self.dry_run, self._config)
        self.run_data = RunDataManager(cfg.get_max_curves(self._config))
        self._handlers = {}
        self._state_lock = threading.RLock()
        self._cancel = threading.Event()
        self._worker = None
        self._stop_reason = "STOPPED"
        self._closed = False
        self._closing = False
        self._hardware_lease = None
        self._manual_id = None
        self._thermal_pending = None
        self._thermal_reading = None
        self._restore_graph()
        self.store.retry_exports()

    def on(self, event, handler):
        with self._state_lock:
            self._handlers.setdefault(event, []).append(handler)

    def off(self, event, handler):
        with self._state_lock:
            if handler in self._handlers.get(event, []):
                self._handlers[event].remove(handler)

    def _emit(self, event, *args):
        with self._state_lock:
            handlers = list(self._handlers.get(event, []))
        for handler in handlers:
            try:
                handler(*copy.deepcopy(args))
            except Exception:
                logger.exception("UI observer failed: %s", event)

    def _status(self):
        self._emit("status_update", self.manager.get_status_dict())

    def _claim(self, operation, allow_fault=False, require_connection=True):
        with self._state_lock:
            if self._closed or (self._closing and operation != "disconnect"):
                raise BusyError("Application is closing")
            if self.manager.busy:
                raise BusyError(f"Busy: {self.manager.busy}")
            if self.manager.fault and not allow_fault:
                raise SafetyError(
                    "Fault latched; acknowledge after checking hardware: "
                    + self.manager.fault
                )
            if require_connection and not self.manager.connected:
                raise ConnectionError("Connect both instruments first")
            self.manager.busy = operation
            self._cancel.clear()
            self._stop_reason = "STOPPED"

    def _release(self):
        with self._state_lock:
            self.manager.busy = ""
            self.manager.status = (
                "fault"
                if self.manager.fault
                else ("idle" if self.manager.connected else "disconnected")
            )
        self._status()

    def _fault(self, message):
        self.manager.fault = str(message)
        self.manager.status = "fault"
        try:
            self._tlog.write("ERROR", message=str(message))
        except Exception:
            logger.exception("Diagnostic log failed")

    def _state(self, state):
        if state in ("PSU_ON", "PSU_OFF"):
            self.manager.psu_on = None
        if state in ("LOAD_ON", "LOAD_OFF"):
            self.manager.load_on = None
        self.manager.status = state.lower()
        self._tlog.write("STATE", state=state)
        self._status()

    def _action(self, fn):
        with hardware_lock:
            check_cancel(self._cancel)
            value = fn()
            check_cancel(self._cancel)
            return value

    def _acquire_hardware(self):
        if self.dry_run or self._hardware_lease:
            return
        path = hardware_lock_path()
        handle = open(path, "a")
        try:
            lock_exclusive(handle)
        except Exception:
            handle.close()
            raise BusyError("Another tester process owns the instruments")
        self._hardware_lease = handle

    def _release_hardware(self):
        if self._hardware_lease:
            self._hardware_lease.close()
            self._hardware_lease = None

    def _shutdown(self):
        errors = []
        with hardware_lock:
            for name, device, method in [
                ("Load", self.manager.load, "input_off"),
                ("PSU", self.manager.psu, "output_off"),
            ]:
                try:
                    if device:
                        getattr(device, method)()
                        if name == "Load":
                            self.manager.load_on = False
                        else:
                            self.manager.psu_on = False
                except Exception as exc:
                    errors.append(f"{name} OFF unverified: {exc}")
                    if name == "Load":
                        self.manager.load_on = None
                    else:
                        self.manager.psu_on = None
            if self.manager.load and self.manager.load_on is False:
                try:
                    self.manager.load.set_cc_current(0)
                except Exception as exc:
                    errors.append(f"Load zero-current failed: {exc}")
        if errors:
            raise ShutdownError("; ".join(errors))

    def connect(self, psu_port=None, load_port=None):
        self._claim("connect", allow_fault=True, require_connection=False)
        if self.manager.connected:
            self._release()
            raise BusyError("Disconnect before reconnecting")
        try:
            self._acquire_hardware()
            if self.dry_run:
                self.manager.psu = SimPSU()
                self.manager.load = SimLoad(
                    self.manager.psu,
                    self.manager.safety_limits["nominal_output_voltage"],
                )
            else:
                ports = cfg.get_serial_ports(self._config)
                explicit = psu_port is not None or load_port is not None
                psu_port = psu_port or ports.get("psu")
                load_port = load_port or ports.get("load")
                if not explicit and not (
                    psu_port
                    and load_port
                    and discovery.port_available(psu_port)
                    and discovery.port_available(load_port)
                ):
                    found = discovery.scan_all(
                        cfg.get_baudrate(self._config),
                        cfg.get_serial_timeout(self._config),
                    )
                    choices = {
                        kind: [r["port"] for r in found if r["device_type"] == kind]
                        for kind in ("psu", "load")
                    }
                    if any(len(v) != 1 for v in choices.values()):
                        raise ConnectionError(
                            "Select one identified PSU and load; discovery is missing or ambiguous"
                        )
                    psu_port, load_port = choices["psu"][0], choices["load"][0]
                if (
                    not psu_port
                    or not load_port
                    or discovery.port_key(psu_port) == discovery.port_key(load_port)
                ):
                    raise ConnectionError(
                        "PSU and load require different physical ports"
                    )
                for kind, port, cls in [
                    ("psu", psu_port, OwonPSU),
                    ("load", load_port, OwonLoad),
                ]:
                    res = SerialResource(
                        port,
                        cfg.get_baudrate(self._config),
                        cfg.get_serial_timeout(self._config),
                        log=lambda direction, port, value: self._tlog.write(
                            direction, port=port, value=value
                        ),
                    )
                    try:
                        res.open()
                        idn = res.query("*IDN?")
                        if discovery._classify(idn) != kind:
                            raise ConnectionError(
                                f"{port}: expected {kind}, received {idn!r}"
                            )
                        expected = self._config.get("instruments", {}).get(
                            kind + "_idn"
                        )
                        if expected and expected != idn:
                            raise ConnectionError(
                                f"{kind} identity differs from configured ID"
                            )
                        kwargs = {"cmd_retries": cfg.get_cmd_retries(self._config)}
                        if kind == "psu":
                            kwargs["output_state_format"] = self._config[
                                "instruments"
                            ].get("psu_output_state_format", "auto")
                        device = cls(res, **kwargs)
                        setattr(self.manager, kind, device)
                        device.set_remote()
                    except Exception:
                        res.close()
                        raise
            self.manager.identity = {
                kind: {
                    "idn": getattr(self.manager, kind).identify(),
                    "port": getattr(self.manager, kind).port(),
                }
                for kind in ("psu", "load")
            }
            self._shutdown()
            self.manager.connected = True
            # Connecting does not silently acknowledge a prior fault.
            return True
        except Exception as exc:
            try:
                self._shutdown()
            except Exception as shutdown:
                self._fault(f"{exc}; {shutdown}")
            else:
                self._fault(exc)
            self._close_ports()
            raise
        finally:
            self._release()

    def _close_ports(self):
        for kind in ("load", "psu"):
            device = getattr(self.manager, kind)
            if device:
                try:
                    device.disconnect()
                except Exception:
                    logger.exception("Port close failed")
            setattr(self.manager, kind, None)
        self.manager.connected = False
        self._release_hardware()

    def scan_ports(self):
        self._claim("scan", allow_fault=True, require_connection=False)
        try:
            if self.manager.connected:
                raise BusyError("Disconnect before scanning serial ports")
            self._acquire_hardware()
            return discovery.scan_all(
                cfg.get_baudrate(self._config), cfg.get_serial_timeout(self._config)
            )
        finally:
            if not self.manager.connected:
                self._release_hardware()
            self._release()

    def request_stop(self, reason="STOPPED"):
        # Safe to call directly on the GUI thread: no I/O, no join.
        with self._state_lock:
            if self._stop_reason != "EMERGENCY_STOP":
                self._stop_reason = reason
            self._cancel.set()
            if reason == "EMERGENCY_STOP":
                self.manager.fault = (
                    "Emergency stop requested; acknowledge after checking hardware"
                )

    def stop_sweep(self):
        if self.manager._sweep_state != "idle":
            self.request_stop("STOPPED")

    def abort_production(self):
        if self.manager._prod_state != "idle":
            self.request_stop("ABORTED")

    def pause_sweep(self):
        raise BusyError("Pause is not supported. Stop and restart the run.")

    def resume_sweep(self):
        raise BusyError("Resume is not supported. Start a new run.")

    def emergency_stop(self):
        self.request_stop("EMERGENCY_STOP")
        with self._state_lock:
            claimed = not self.manager.busy
            if claimed:
                self.manager.busy = "emergency shutdown"
        try:
            if claimed and self._manual_id is not None:
                self._finish(
                    self._manual_id, "EMERGENCY_STOP", "Emergency stop requested"
                )
                self._manual_id = None
                if (
                    self.manager.psu_on is not False
                    or self.manager.load_on is not False
                ):
                    raise ShutdownError(self.manager.fault)
            else:
                self._shutdown()
        except Exception as exc:
            self._fault(exc)
            raise
        finally:
            if claimed:
                self._release()
            self._emit("emergency_stop")
            self._status()

    def acknowledge_fault(self):
        self._claim("acknowledge", allow_fault=True)
        try:
            self._shutdown()
            self.manager.fault = ""
        except Exception as exc:
            self._fault(exc)
            raise
        finally:
            self._release()

    def poll_status(self):
        with self._state_lock:
            if self.manager.busy or not self.manager.connected or self.manager.fault:
                return self.manager.get_status_dict()
        try:
            self._claim("status")
        except (BusyError, SafetyError):
            return self.manager.get_status_dict()
        try:
            p = take_measurement(
                self.manager.psu, self.manager.load, "status", cancel=self._cancel
            )
            self.manager.psu_on = self._action(self.manager.psu.output_status)
            self.manager.load_on = self._action(self.manager.load.input_status)
            if self.manager.psu_on:
                check_measurement(
                    p,
                    self.manager.safety_limits,
                    self.manager.load_on and self.manager.load.get_cc_current() > 0.01,
                )
            self.manager.readings = p
        except Exception as exc:
            self.manager.readings = None
            self._fault(exc)
            if self._manual_id is not None:
                self._finish(self._manual_id, "ERROR", str(exc))
                self._manual_id = None
            try:
                self._shutdown()
            except Exception as shutdown:
                self._fault(f"{exc}; {shutdown}")
        finally:
            self._release()
        return self.manager.get_status_dict()

    def _manual_command(self, fn, allow_fault=False):
        self._claim("manual control", allow_fault=allow_fault)
        try:
            return self._action(fn)
        except Exception as exc:
            self._fault(exc)
            if self._manual_id is not None:
                self._finish(self._manual_id, "ERROR", str(exc))
                self._manual_id = None
            try:
                self._shutdown()
            except Exception as shutdown:
                self._fault(f"{exc}; {shutdown}")
            raise
        finally:
            self._release()

    def set_psu(self, voltage, current):
        voltage = number(voltage, "Input voltage")
        current = number(current, "Input current")
        require_limits(
            voltage=voltage, current=current, config=self.manager.safety_limits
        )

        def apply():
            self.manager.load.input_off()
            self.manager.load_on = False
            self.manager.psu.output_off()
            self.manager.psu_on = False
            check_cancel(self._cancel)
            self.manager.psu.set_voltage(voltage)
            self.manager.psu.set_current_limit(current)
            self.manager.manual_psu_set_voltage = voltage
            self.manager.manual_psu_current_limit = current

        return self._manual_command(apply)

    def psu_on(self):
        def apply():
            v = self.manager.manual_psu_set_voltage
            i = self.manager.manual_psu_current_limit
            if v is None or i is None:
                raise SafetyError("Set PSU voltage and current before enabling")
            require_limits(voltage=v, current=i, config=self.manager.safety_limits)
            self.manager.load.input_off()
            self.manager.load_on = False
            self.manager.psu.output_off()
            self.manager.psu_on = False
            check_cancel(self._cancel)
            self.manager.psu.set_voltage(v)
            self.manager.psu.set_current_limit(i)
            check_cancel(self._cancel)
            self.manager.psu.output_on()
            check_cancel(self._cancel)
            self.manager.psu.set_current_limit(i)
            self.manager.psu_on = True

        return self._manual_command(apply)

    def psu_off(self):
        def apply():
            self.manager.psu.output_off()
            self.manager.psu_on = False

        return self._manual_command(apply, True)

    def set_load(self, current):
        current = number(current, "Load current")
        require_limits(
            load_current=current,
            output_power=current * self.manager.safety_limits["nominal_output_voltage"],
            config=self.manager.safety_limits,
        )

        def apply():
            self.manager.load.input_off()
            self.manager.load_on = False
            self.manager.load.set_function("CURRent")
            self.manager.load.set_mode("NORM")
            self.manager.load.set_cc_current(current)

        return self._manual_command(apply)

    def load_on(self):
        def apply():
            if not self.manager.psu.output_status():
                raise SafetyError("Enable the PSU first")
            current = number(self.manager.load.get_cc_current(), "Load current")
            require_limits(
                load_current=current,
                output_power=current
                * self.manager.safety_limits["nominal_output_voltage"],
                config=self.manager.safety_limits,
            )
            self.manager.load.input_off()
            self.manager.load_on = False
            self.manager.load.set_function("CURRent")
            self.manager.load.set_mode("NORM")
            self.manager.load.set_cc_current(current)
            check_cancel(self._cancel)
            self.manager.load.input_on()
            self.manager.load_on = True

        return self._manual_command(apply)

    def load_off(self):
        def apply():
            self.manager.load.input_off()
            self.manager.load_on = False

        return self._manual_command(apply, True)

    def _steps(self, params, production=False):
        if not isinstance(params, dict):
            raise SafetyError("Run parameters must be an object")
        if production and not isinstance(params.get("continue_on_fail", False), bool):
            raise SafetyError("continue_on_fail must be boolean")
        steps = params.get("steps", [])
        if not isinstance(steps, list) or not 1 <= len(steps) <= 1000:
            raise SafetyError("A run requires 1–1000 steps")
        nominal = number(
            params.get(
                "nominal_output_voltage",
                self.manager.safety_limits["nominal_output_voltage"],
            ),
            "Nominal output voltage",
            0.001,
        )
        out = []
        for idx, raw in enumerate(steps):
            if not isinstance(raw, dict):
                raise SafetyError("Each step must be an object")
            power = number(raw.get("requested_power_w"), "Requested power")
            voltage = number(
                raw.get("psu_voltage", params.get("input_voltage")),
                "Input voltage",
                0.001,
            )
            current = number(
                raw.get("input_current_limit", params.get("input_current_limit")),
                "Input current limit",
                0.001,
            )
            load = (
                power / nominal
                if production
                else number(raw.get("load_current_a", power / nominal), "Load current")
            )
            require_limits(
                voltage=voltage,
                current=current,
                load_current=load,
                output_power=power,
                config=self.manager.safety_limits,
            )
            # Validate current-based demand as well as the supplied power metadata.
            require_limits(
                output_power=load * nominal, config=self.manager.safety_limits
            )
            delay = number(
                raw.get("wait_s", params.get("wait_time", 3)), "Step wait", 0, 60
            )
            if production:
                for key in ("min_vout", "max_vout", "min_efficiency_percent"):
                    if not isinstance(raw.get(key), (int, float)) or isinstance(
                        raw.get(key), bool
                    ):
                        raise SafetyError(f"{key} must be numeric")
                    number(
                        raw.get(key),
                        key,
                        0,
                        105 if key == "min_efficiency_percent" else None,
                    )
                if raw["min_vout"] > raw["max_vout"]:
                    raise SafetyError("Production voltage limits reversed")
            out.append(
                SweepStep(
                    idx,
                    str(raw.get("name", f"Step {idx + 1}")),
                    power,
                    load,
                    voltage,
                    current,
                    delay,
                    str(raw.get("notes", "")),
                )
            )
        return out

    def _create_run(self, mode, params, label="", operator=""):
        rid = self.store.create_test_run(
            mode,
            unit_serial_or_label=label,
            operator=operator,
            profile_name=params.get("name", ""),
            notes=params.get("run_name", ""),
            profile=params,
            config=self._config,
            instruments=self.manager.identity,
            dry_run=self.dry_run,
        )
        try:
            self.manager.csv_path = self.store.export_csv(rid)
        except Exception as exc:
            self.store.update_run_result(
                rid, "ERROR", f"CSV creation failed: {exc}", False
            )
            self._fault(exc)
            raise
        return rid

    def _record(self, rid, point, tab):
        point.update(
            run_id=rid, run_name=self.store.get_run(rid)["notes"] or f"Run #{rid}"
        )
        self.store.insert_test_point(rid, point)
        self.manager.csv_path = self.store.export_csv(rid)
        getattr(
            self.manager,
            {
                "manual": "manual_points",
                "sweep": "sweep_points",
                "production": "prod_points",
            }[tab],
        ).append(point)
        self.manager.readings = point
        if tab != "production":
            self._emit("measurement", tab, point)

    def _finish(self, rid, result, reason=""):
        verified = False
        try:
            self._shutdown()
            verified = True
        except Exception as exc:
            result = "ERROR"
            reason = f"{reason}; {exc}".strip("; ")
            self._fault(reason)
        if self._cancel.is_set() and result in (
            "COMPLETE",
            "PASS",
            "ENGINEERING_DATA",
            "FAIL",
        ):
            result = self._stop_reason
            reason = "Stopped by operator"
        persisted = False
        try:
            self.store.update_run_result(rid, result, reason, verified)
            persisted = True
        except Exception as exc:
            result = "ERROR"
            reason = f"{reason}; Result persistence failed: {exc}".strip("; ")
            self._fault(reason)
        # CSV is an export of committed SQLite data, never a speculative PASS.
        if persisted:
            try:
                self.manager.csv_path = self.store.export_csv(rid)
            except Exception as exc:
                warning = f"CSV export pending (SQLite result saved): {exc}"
                self._fault(warning)
                reason = f"{reason}; {warning}".strip("; ")
                try:
                    self.store.set_export_error(rid, str(exc))
                except Exception:
                    logger.exception("Could not record CSV retry metadata")
        if result in ("ERROR", "FAILED", "EMERGENCY_STOP"):
            self._fault(reason or result)
        try:
            self._tlog.write(
                "RESULT",
                run_id=rid,
                result=result,
                reason=reason,
                shutdown_verified=verified,
            )
        except Exception as exc:
            self._fault(f"Diagnostic log failed: {exc}")
        if persisted and result in ("COMPLETE", "PASS", "ENGINEERING_DATA"):
            try:
                self._restore_graph()
            except Exception as exc:
                self._fault(f"Graph restore failed; result is saved: {exc}")
        return result, reason

    def manual_record(self, **params):
        voltage = number(params.get("psu_voltage", 84), "Input voltage")
        current = number(params.get("psu_current_limit", 5), "Input current")
        load = number(params.get("load_current", 0), "Load current")
        power = load * self.manager.safety_limits["nominal_output_voltage"]
        step = self._steps(
            dict(
                input_voltage=voltage,
                input_current_limit=current,
                wait_time=self.manager.settle_time,
                steps=[
                    dict(
                        requested_power_w=power,
                        load_current_a=load,
                        name=params.get("step_name", "Manual"),
                    )
                ],
            )
        )[0]
        self._claim("manual recording")
        try:
            if self._manual_id is None:
                self._manual_id = self._create_run(
                    "manual", dict(run_name="Manual capture")
                )
                self.manager.manual_run_id = self._manual_id
            runner = SweepRunner(
                self.manager.psu,
                self.manager.load,
                self._cancel,
                self._state,
                cfg.get_sample_count(self._config),
                self.manager.safety_limits,
            )
            runner.prepare(step)
            self.manager.psu_on = True
            self.manager.load_on = load > 0
            point = runner.measure(step, "manual")
            point["step_index"] = len(self.manager.manual_points)
            check_measurement(point, self.manager.safety_limits, load > 0.01)
            check_cancel(self._cancel)
            self._record(self._manual_id, point, "manual")
            return point
        except Exception as exc:
            self._fault(exc)
            if self._manual_id is not None:
                self._finish(
                    self._manual_id,
                    self._stop_reason if isinstance(exc, Cancelled) else "ERROR",
                    str(exc),
                )
                self._manual_id = None
            else:
                try:
                    self._shutdown()
                except Exception as shutdown:
                    self._fault(shutdown)
            raise
        finally:
            self._release()

    def manual_start_new_run(self):
        self._claim("finish manual", allow_fault=True)
        try:
            if self._manual_id is not None:
                self._finish(
                    self._manual_id,
                    "ERROR" if self.manager.fault else "COMPLETE",
                    self.manager.fault,
                )
                self._manual_id = None
            else:
                try:
                    self._shutdown()
                except Exception as exc:
                    self._fault(exc)
                    raise
            self.manager.manual_run_id = None
            self.manager.manual_points = []
            self._emit("clear_run", "manual")
        finally:
            self._release()

    def manual_clear(self):
        self._claim("clear manual", allow_fault=True, require_connection=False)
        try:
            self.manager.manual_points = []
            self._emit("clear_run", "manual")
        finally:
            self._release()

    def start_sweep(self, params):
        steps = self._steps(params)
        self._start("sweep", params, steps)
        return True

    def start_production(self, label, profile=None, operator="", thermal_check=None):
        if not isinstance(label, str) or not label.strip():
            return False, "PCB label is required", None
        if len(label) > 200 or any(ord(c) < 32 for c in label):
            return False, "Invalid PCB label", None
        profile = copy.deepcopy(
            profile if profile is not None else self.get_production_profile()
        )
        try:
            if not profile:
                raise SafetyError("No production profile")
            profile["thermal_check"] = thermal_settings(
                thermal_check if thermal_check is not None else profile.get("thermal_check")
            )
            steps = self._steps(profile, True)
            if profile["thermal_check"]["enabled"] and steps[-1].load_current_a <= 0:
                raise SafetyError("Thermal check requires a nonzero final load")
            rid = self._start("production", profile, steps, label.strip(), operator)
            return True, None, rid
        except (ValueError, BusyError, ConnectionError) as exc:
            return False, str(exc), None

    def get_thermal_check(self):
        with self._state_lock:
            return copy.deepcopy(self._thermal_pending)

    def submit_thermal_reading(self, run_id, maximum_c):
        maximum = number(maximum_c, "Measured thermal maximum °C", -20, 550)
        with self._state_lock:
            pending = self._thermal_pending
            if (not pending or pending["run_id"] != run_id
                    or pending["phase"] != "awaiting_reading" or self._cancel.is_set()
                    or time.monotonic() >= pending["deadline"]):
                raise BusyError("No thermal reading is currently requested for this run")
            if self._thermal_reading is not None:
                raise BusyError("A thermal reading has already been submitted")
            self._thermal_reading = (maximum, timestamp())
        return True

    def _run_thermal_check(self, rid, params, step, label):
        settings = params["thermal_check"]
        limit = settings["max_temp_c"]
        with self._state_lock:
            self._thermal_reading = None
        try:
            for phase, duration in (("warming", settings["warmup_s"]),
                                    ("awaiting_reading", THERMAL_INPUT_TIMEOUT_S)):
                deadline = time.monotonic() + duration
                with self._state_lock:
                    self._thermal_pending = dict(
                        run_id=rid, label=label, phase=phase, max_temp_c=limit,
                        power_w=step.requested_power_w, deadline=deadline,
                        remaining_s=duration,
                    )
                self.store.record_thermal(rid, "WARMING" if phase == "warming" else "WAITING")
                self._state("THERMAL_" + phase.upper())
                while True:
                    check_cancel(self._cancel)
                    with self._state_lock:
                        reading = self._thermal_reading
                        remaining = max(0, deadline - time.monotonic())
                        self._thermal_pending["remaining_s"] = remaining
                        pending = copy.deepcopy(self._thermal_pending)
                    self._emit("thermal_check", pending)
                    if phase == "awaiting_reading" and reading is not None:
                        maximum, measured_at = reading
                        self._shutdown()
                        passed = maximum <= limit
                        self.store.record_thermal(
                            rid, "PASS" if passed else "FAIL", maximum,
                            "operator_entered", measured_at,
                        )
                        return passed, f"Thermal maximum {maximum:g}°C {'≤' if passed else '>'} {limit:g}°C limit"
                    if remaining <= 0:
                        break
                    # Keep electrical protection checks active while the final load is held.
                    point = take_measurement(
                        self.manager.psu, self.manager.load, "production_test", label,
                        step_name="Thermal hold", requested_power_w=step.requested_power_w,
                        psu_set_voltage=step.psu_voltage, psu_current_limit=step.psu_current_limit,
                        load_set_current=step.load_current_a, samples=1, cancel=self._cancel,
                        safety_limits=self.manager.safety_limits,
                    )
                    self.manager.readings = point
                    passed, reason = check_production_step(
                        point["vout"], point["efficiency_percent"], params["steps"][-1],
                    )
                    if not passed:
                        self._shutdown()
                        self.store.record_thermal(rid, "INCOMPLETE")
                        return False, "Electrical failure during thermal hold: " + reason
                    self._cancel.wait(min(0.5, max(0, deadline - time.monotonic())))
            self._shutdown()
            self.store.record_thermal(rid, "MISSING")
            return False, "Thermal check failed: no maximum temperature entered within 60 seconds"
        except Cancelled:
            self.store.record_thermal(rid, "ABORTED")
            raise
        except Exception:
            self.store.record_thermal(rid, "ERROR")
            raise
        finally:
            with self._state_lock:
                self._thermal_pending = None
                self._thermal_reading = None
            self._emit("thermal_check", None)

    def _start(self, tab, params, steps, label="", operator=""):
        self._claim(tab)
        rid = None
        try:
            if self._manual_id is not None:
                self._finish(self._manual_id, "COMPLETE")
                self._manual_id = None
                if self.manager.fault:
                    raise SafetyError(self.manager.fault)
            rid = self._create_run(
                "automated_sweep" if tab == "sweep" else "production_test",
                params,
                label,
                operator,
            )
            setattr(
                self.manager, "sweep_run_id" if tab == "sweep" else "prod_run_id", rid
            )
            setattr(
                self.manager, "sweep_points" if tab == "sweep" else "prod_points", []
            )
            setattr(
                self.manager,
                "_sweep_state" if tab == "sweep" else "_prod_state",
                "running",
            )
            self._emit("clear_run", tab)
            self._worker = threading.Thread(
                target=self._run,
                args=(tab, rid, copy.deepcopy(params), steps, label),
                daemon=False,
            )
            self._worker.start()
            return rid
        except Exception as exc:
            self._fault(exc)
            if rid is not None:
                self._worker = None
                try:
                    self._finish(rid, "ERROR", f"Could not start worker: {exc}")
                finally:
                    self._release()
            else:
                try:
                    self._shutdown()
                except Exception as shutdown:
                    self._fault(f"{exc}; {shutdown}")
            setattr(
                self.manager,
                "_sweep_state" if tab == "sweep" else "_prod_state",
                "idle",
            )
            self._release()
            raise

    def _run(self, tab, rid, params, steps, label):
        result = "COMPLETE" if tab == "sweep" else "PASS"
        reason = ""
        runner = SweepRunner(
            self.manager.psu,
            self.manager.load,
            self._cancel,
            self._state,
            cfg.get_sample_count(self._config),
            self.manager.safety_limits,
        )
        try:
            previous = None
            for step in steps:
                check_cancel(self._cancel)
                runner.prepare(step, previous)
                self.manager.psu_on = True
                self.manager.load_on = step.load_current_a > 0
                point = runner.measure(
                    step,
                    "automated_sweep" if tab == "sweep" else "production_test",
                    label,
                )
                try:
                    check_measurement(
                        point, self.manager.safety_limits, step.load_current_a > 0.01
                    )
                except SafetyError as exc:
                    point.update(pass_fail_status="FAULT", notes=str(exc))
                    self._record(rid, point, tab)
                    if tab == "production":
                        self._emit("prod_step", step.name, "FAULT", str(exc), point)
                    raise
                check_cancel(self._cancel)
                passed = True
                failure = None
                if tab == "production":
                    passed, failure = check_production_step(
                        point["vout"],
                        point["efficiency_percent"],
                        params["steps"][step.index],
                    )
                    point.update(
                        pass_fail_status="PASS" if passed else "FAIL",
                        notes=failure or "",
                    )
                    if not passed:
                        result = "FAIL"
                        reason += "; " + step.name + ": " + failure
                self._record(rid, point, tab)
                if tab == "production":
                    self._emit(
                        "prod_step",
                        step.name,
                        point["pass_fail_status"],
                        failure,
                        point,
                    )
                if not passed and not params.get("continue_on_fail", False):
                    break
                previous = step
            if tab == "production" and result == "PASS" and params["thermal_check"]["enabled"]:
                passed, thermal_reason = self._run_thermal_check(rid, params, steps[-1], label)
                if not passed:
                    result = "FAIL"
                    reason = thermal_reason
                self._emit("prod_step", "Thermal check", "PASS" if passed else "FAIL", thermal_reason, None)
        except Cancelled:
            result = self._stop_reason
            reason = "Stopped by operator"
        except Exception as exc:
            result = "ERROR"
            reason = str(exc)
            self._fault(reason)
        finally:
            # A stop arriving on the last step must not be promoted to PASS/COMPLETE.
            if self._cancel.is_set() and result in ("COMPLETE", "PASS", "FAIL"):
                result = self._stop_reason
                reason = "Stopped by operator"
            try:
                result, reason = self._finish(rid, result, reason.strip("; "))
            except Exception as exc:
                result = "ERROR"
                reason = str(exc)
                self._fault(reason)
            setattr(
                self.manager,
                "_sweep_state" if tab == "sweep" else "_prod_state",
                "idle",
            )
            self._release()
            if tab == "production":
                self._emit("prod_result", result, reason, label)
            elif result == "COMPLETE":
                self._emit("sweep_done")
            else:
                self._emit("sweep_error", f"{result}: {reason}")

    def wait_idle(self, timeout=30):
        worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout)
        if worker and worker.is_alive():
            raise BusyError("Worker has not stopped; hardware state is not confirmed")

    def disconnect(self):
        self.request_stop("ABORTED")
        self.wait_idle()
        self._claim("disconnect", allow_fault=True, require_connection=False)
        try:
            if self._manual_id is not None:
                self._finish(
                    self._manual_id,
                    "ERROR" if self.manager.fault else "COMPLETE",
                    self.manager.fault,
                )
                self._manual_id = None
            self._shutdown()
        except Exception as exc:
            self._fault(exc)
            raise
        finally:
            self._close_ports()
            self._release()

    def close(self):
        if self._closed:
            return
        self._closing = True
        try:
            self.disconnect()
        finally:
            # Do not release storage while a worker is still using it.
            if not self.manager.busy and not (self._worker and self._worker.is_alive()):
                self._tlog.close()
                self.store.close()
                self._closed = True

    def clear_sweep(self):
        self._claim("clear sweep", allow_fault=True, require_connection=False)
        try:
            self.manager.sweep_points = []
            self.run_data.deselect_all()
            self._emit("clear_run", "sweep")
            self._emit("graph_data_changed")
        finally:
            self._release()

    def start_new_sweep_run(self):
        self.clear_sweep()

    def _restore_graph(self):
        runs = self.store.get_recent_runs(10000)
        for run in runs:
            if run["final_result"] in ("COMPLETE", "PASS", "ENGINEERING_DATA"):
                run["points"] = self.store.get_run_points(run["id"])
        self.run_data.restore_runs(runs)
        if hasattr(self, "_handlers"):
            self._emit("graph_data_changed")

    def get_completed_runs(self):
        return [
            dict(
                run_id=r.run_id,
                run_name=r.label,
                input_voltage=r.input_voltage,
                points=r.points,
                timestamp=r.timestamp,
            )
            for r in self.run_data.completed_runs
        ]

    def get_selected_runs(self):
        return [
            dict(run_id=r.run_id, run_name=r.label, points=r.points)
            for r in self.run_data.selected_runs
        ]

    def toggle_run_selection(self, rid):
        result = self.run_data.toggle_selection(rid)
        self._emit("graph_data_changed")
        return result

    def select_all_runs(self):
        self.run_data.select_all()
        self._emit("graph_data_changed")

    def deselect_all_runs(self):
        self.run_data.deselect_all()
        self._emit("graph_data_changed")

    def get_all_measurements(self):
        return dict(
            manual=copy.deepcopy(self.manager.manual_points),
            production=copy.deepcopy(self.manager.prod_points),
            sweep=[p for r in self.get_completed_runs() for p in r["points"]],
        )

    def get_runs(self, limit=50):
        return self.store.get_recent_runs(limit)

    def delete_run(self, rid):
        self._claim("delete run", allow_fault=True, require_connection=False)
        try:
            self.store.delete_run(rid)
            self.run_data.remove(rid)
            self._emit("graph_data_changed")
        finally:
            self._release()

    def get_production_profile(self):
        return copy.deepcopy(self._config.get("production_test_profile"))

    def save_production_profile(self, profile):
        self.save_production_settings(profile, self._config.get("result_sounds_enabled", True))

    def save_production_settings(self, profile, sounds_enabled):
        profile = copy.deepcopy(profile)
        if not isinstance(profile, dict) or not str(profile.get('name', '')).strip():
            raise ValueError("Profile name is required")
        profile["thermal_check"] = thermal_settings(profile.get("thermal_check"))
        steps = self._steps(profile, True)
        if profile['thermal_check']['enabled'] and steps[-1].load_current_a <= 0:
            raise ValueError("Thermal check requires a nonzero final load")
        updated = copy.deepcopy(self._config)
        updated["production_test_profile"] = profile
        updated["result_sounds_enabled"] = sounds_enabled
        self.save_configuration(updated)

    def save_configuration(self, config):
        self._claim("save configuration", allow_fault=True, require_connection=False)
        try:
            if self.manager.connected:
                raise BusyError("Disconnect before changing configuration")
            validated = cfg.validate_config(config)
            if cfg.is_dry_run(validated) != self.dry_run:
                raise ValueError("Restart to change simulation mode")
            if any(
                validated.get(key) != self._config.get(key)
                for key in ("data_dir", "log_dir")
            ):
                raise ValueError(
                    "Change storage paths in config.json while the application is closed"
                )
            self._config = cfg.save_config(validated)
            self.manager._config = self._config
            self.run_data.max_visible = cfg.get_max_curves(self._config)
            self.run_data.deselect_all()
        finally:
            self._release()
