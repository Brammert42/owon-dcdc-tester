"""Instance-scoped SQLite storage, additive migrations, terminal results and CSV export."""

import csv
import datetime
import json
import os
from pathlib import Path
import sqlite3
import uuid
from .file_lock import lock_exclusive, unlock
from app_paths import runtime_root

BASE_DIR = runtime_root()
DB_PATH = BASE_DIR / "data/test_results.sqlite"


class _ClosingConnection(sqlite3.Connection):
    """Close SQLite handles used as context managers on every platform."""

    def __exit__(self, *args):
        result = super().__exit__(*args)
        self.close()
        return result


def timestamp():
    return datetime.datetime.now().astimezone().isoformat()


class Store:
    def __init__(self, path, csv_dir):
        self.path, self.csv_dir = Path(path), Path(csv_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.session = uuid.uuid4().hex
        self._lease = open(str(self.path) + ".lock", "a")
        try:
            lock_exclusive(self._lease)
            self._initialize()
        except Exception:
            self._lease.close()
            raise

    def connection(self):
        c = sqlite3.connect(self.path, timeout=5, factory=_ClosingConnection)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def _initialize(self):
        c = self.connection()
        try:
            tables = {
                r[0]
                for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "test_runs" in tables:
                columns = {r[1] for r in c.execute("PRAGMA table_info(test_runs)")}
                if "session_id" not in columns:
                    backup = sqlite3.connect(
                        str(self.path) + ".before-robustness-" + self.session + ".bak"
                    )
                    try:
                        c.backup(backup)
                    finally:
                        backup.close()
            c.executescript("""
                CREATE TABLE IF NOT EXISTS test_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_start TEXT NOT NULL,
                    timestamp_end TEXT, test_mode TEXT NOT NULL, unit_serial_or_label TEXT,
                    profile_name TEXT, operator TEXT, final_result TEXT, fail_reason TEXT, notes TEXT);
                CREATE TABLE IF NOT EXISTS test_points (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
                    timestamp TEXT NOT NULL, step_index INTEGER, step_name TEXT,
                    requested_power_w REAL, psu_set_voltage REAL, input_voltage_group REAL,
                    psu_current_limit REAL, load_set_current REAL, vin REAL, iin REAL, pin REAL,
                    vout REAL, iout REAL, pout REAL, efficiency_percent REAL,
                    step_result TEXT, fail_reason TEXT, FOREIGN KEY(run_id) REFERENCES test_runs(id));
            """)
            for table, fields in {
                "test_runs": {
                    "session_id": "TEXT",
                    "dry_run": "INTEGER",
                    "profile_json": "TEXT",
                    "config_json": "TEXT",
                    "instrument_json": "TEXT",
                    "csv_path": "TEXT",
                    "shutdown_verified": "INTEGER",
                    "software_version": "TEXT",
                    "csv_export_error": "TEXT",
                    "thermal_enabled": "INTEGER",
                    "thermal_limit_c": "REAL",
                    "thermal_warmup_s": "REAL",
                    "thermal_max_c": "REAL",
                    "thermal_result": "TEXT",
                    "thermal_source": "TEXT",
                    "thermal_timestamp": "TEXT",
                },
                "test_points": {
                    "samples": "INTEGER",
                    "valid": "INTEGER",
                    "input_voltage_group": "REAL",
                },
            }.items():
                have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                for name, kind in fields.items():
                    if name not in have:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
            # Only recover sessions written by this implementation. Historical NULL outcomes stay unknown.
            c.execute(
                "UPDATE test_runs SET final_result='INTERRUPTED', csv_export_error='pending', timestamp_end=?, fail_reason=? "
                "WHERE final_result IS NULL AND session_id IS NOT NULL",
                (
                    timestamp(),
                    "Application ended before verified completion; output state unknown",
                ),
            )
            c.commit()
        finally:
            c.close()

    def create_test_run(
        self,
        test_mode,
        unit_serial_or_label=None,
        profile_name=None,
        operator="",
        notes="",
        profile=None,
        config=None,
        instruments=None,
        dry_run=False,
    ):
        c = self.connection()
        try:
            with c:
                cur = c.execute(
                    """INSERT INTO test_runs
                    (timestamp_start,test_mode,unit_serial_or_label,profile_name,operator,notes,
                     session_id,dry_run,profile_json,config_json,instrument_json,software_version)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        timestamp(),
                        test_mode,
                        unit_serial_or_label,
                        profile_name,
                        operator,
                        notes,
                        self.session,
                        int(dry_run),
                        json.dumps(profile, allow_nan=False),
                        json.dumps(config, allow_nan=False),
                        json.dumps(instruments, allow_nan=False),
                        "desktop-robustness-1",
                    ),
                )
                rid = cur.lastrowid
                if test_mode == "production_test":
                    thermal = (profile or {}).get("thermal_check", {})
                    enabled = thermal.get("enabled", False)
                    c.execute(
                        "UPDATE test_runs SET thermal_enabled=?,thermal_limit_c=?,"
                        "thermal_warmup_s=?,thermal_result=? WHERE id=?",
                        (int(enabled), thermal.get("max_temp_c", 80),
                         thermal.get("warmup_s", 30), "NOT_RUN" if enabled else "DISABLED", rid),
                    )
                path = self.csv_dir / f"dcdc_{rid}_{self.session[:8]}.csv"
                c.execute(
                    "UPDATE test_runs SET csv_path=? WHERE id=?", (str(path), rid)
                )
            return rid
        finally:
            c.close()

    def insert_test_point(self, run_id, point):
        keys = [
            "timestamp",
            "step_index",
            "step_name",
            "requested_power_w",
            "psu_set_voltage",
            "input_voltage_group",
            "psu_current_limit",
            "load_set_current",
            "vin",
            "iin",
            "pin",
            "vout",
            "iout",
            "pout",
            "efficiency_percent",
            "samples",
            "valid",
        ]
        c = self.connection()
        try:
            with c:
                run = c.execute(
                    "SELECT final_result FROM test_runs WHERE id=?", (run_id,)
                ).fetchone()
                if run is None or run["final_result"] is not None:
                    raise RuntimeError("Run is already terminal")
                columns = ["run_id"] + keys + ["step_result", "fail_reason"]
                values = (
                    [run_id]
                    + [point.get(k) for k in keys]
                    + [point.get("pass_fail_status"), point.get("notes", "")]
                )
                c.execute(
                    f"INSERT INTO test_points ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    values,
                )
        finally:
            c.close()

    def update_run_result(
        self, run_id, final_result, fail_reason=None, shutdown_verified=False
    ):
        c = self.connection()
        try:
            with c:
                cur = c.execute(
                    """UPDATE test_runs SET timestamp_end=?,final_result=?,fail_reason=?,shutdown_verified=?,csv_export_error='pending'
                    WHERE id=? AND final_result IS NULL""",
                    (
                        timestamp(),
                        final_result,
                        fail_reason,
                        int(shutdown_verified),
                        run_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("Refusing to overwrite a terminal run result")
        finally:
            c.close()

    def get_recent_runs(self, limit=50):
        c = self.connection()
        try:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM test_runs ORDER BY id DESC LIMIT ?", (limit,)
                )
            ]
        finally:
            c.close()

    def record_thermal(self, run_id, result, maximum=None, source=None, measured_at=None):
        c = self.connection()
        try:
            with c:
                cur = c.execute(
                    "UPDATE test_runs SET thermal_result=?,thermal_max_c=?,thermal_source=?,"
                    "thermal_timestamp=? WHERE id=? AND final_result IS NULL AND thermal_enabled=1",
                    (result, maximum, source, measured_at, run_id),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("Thermal result requires an active run with thermal check enabled")
        finally:
            c.close()

    def get_run_points(self, run_id):
        c = self.connection()
        try:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM test_points WHERE run_id=? ORDER BY id", (run_id,)
                )
            ]
        finally:
            c.close()

    def get_run(self, run_id):
        c = self.connection()
        try:
            row = c.execute("SELECT * FROM test_runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None
        finally:
            c.close()

    def export_csv(self, run_id):
        run = self.get_run(run_id)
        path = Path(run["csv_path"])
        points = self.get_run_points(run_id)
        base = [
            "run_id",
            "test_mode",
            "unit_serial_or_label",
            "profile_name",
            "operator",
            "dry_run",
            "final_result",
            "run_fail_reason",
            "shutdown_verified",
            "thermal_enabled",
            "thermal_limit_c",
            "thermal_warmup_s",
            "thermal_max_c",
            "thermal_result",
            "thermal_source",
            "thermal_timestamp",
        ]
        keys = [
            "timestamp",
            "step_index",
            "step_name",
            "requested_power_w",
            "psu_set_voltage",
            "input_voltage_group",
            "psu_current_limit",
            "load_set_current",
            "vin",
            "iin",
            "pin",
            "vout",
            "iout",
            "pout",
            "efficiency_percent",
            "step_result",
            "fail_reason",
            "samples",
            "valid",
        ]
        temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("x", newline="", encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=base + keys)
                writer.writeheader()
                for p in points or [{}]:
                    row = {k: run.get(k) for k in base}
                    row.update(run_id=run_id, run_fail_reason=run["fail_reason"])
                    row.update({k: p.get(k) for k in keys})
                    writer.writerow(row)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
        self.set_export_error(run_id, None)
        return str(path)

    def set_export_error(self, run_id, error):
        c = self.connection()
        try:
            with c:
                c.execute(
                    "UPDATE test_runs SET csv_export_error=? WHERE id=?",
                    (error, run_id),
                )
        finally:
            c.close()

    def retry_exports(self):
        for run in self.get_recent_runs(10000):
            if run.get("csv_path") and run.get("csv_export_error"):
                try:
                    self.export_csv(run["id"])
                except Exception as exc:
                    self.set_export_error(run["id"], str(exc))

    def delete_run(self, run_id):
        c = self.connection()
        try:
            with c:
                run = c.execute(
                    "SELECT final_result FROM test_runs WHERE id=?", (run_id,)
                ).fetchone()
                if run and run["final_result"] is None:
                    raise RuntimeError("Cannot delete an active run")
                c.execute("DELETE FROM test_points WHERE run_id=?", (run_id,))
                c.execute("DELETE FROM test_runs WHERE id=?", (run_id,))
        finally:
            c.close()

    def rename_run(self, run_id, name):
        c = self.connection()
        try:
            with c:
                c.execute("UPDATE test_runs SET notes=? WHERE id=?", (name, run_id))
        finally:
            c.close()

    def close(self):
        if not self._lease.closed:
            unlock(self._lease)
            self._lease.close()
