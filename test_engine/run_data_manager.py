"""Thread-safe completed-run storage and explicit graph selection (up to ten)."""

from dataclasses import dataclass
import threading
import copy


@dataclass
class CompletedRun:
    run_id: int
    run_name: str
    input_voltage: float
    points: list
    timestamp: str
    completed: bool = True

    @property
    def label(self):
        return self.run_name or f"Run #{self.run_id}"


class RunDataManager:
    def __init__(self, max_visible=10):
        self.max_visible = min(10, max_visible)
        self._runs = {}
        self._selected = []
        self._lock = threading.RLock()

    @property
    def completed_runs(self):
        with self._lock:
            return copy.deepcopy(
                sorted(self._runs.values(), key=lambda r: r.run_id, reverse=True)
            )

    @property
    def selected_ids(self):
        with self._lock:
            return list(self._selected)

    @property
    def selected_runs(self):
        with self._lock:
            return copy.deepcopy(
                [self._runs[i] for i in self._selected if i in self._runs]
            )

    def is_selected(self, rid):
        return rid in self.selected_ids

    def restore_runs(self, runs):
        with self._lock:
            for r in runs:
                if r.get("final_result") not in (
                    "COMPLETE",
                    "PASS",
                    "ENGINEERING_DATA",
                ):
                    continue
                if not r.get("points"):
                    continue
                rid = r["id"]
                p = r["points"]
                self._runs[rid] = CompletedRun(
                    rid,
                    r.get("notes") or f"{r['test_mode']} #{rid}",
                    p[0].get("psu_set_voltage", 0),
                    p,
                    r["timestamp_start"],
                )

    def toggle_selection(self, rid):
        with self._lock:
            if rid not in self._runs:
                raise ValueError("Only completed runs can be selected")
            if rid in self._selected:
                self._selected.remove(rid)
                return False
            if len(self._selected) >= self.max_visible:
                raise ValueError("Deselect a run before adding another trace")
            self._selected.append(rid)
            return True

    def select_all(self):
        with self._lock:
            self._selected = sorted(self._runs, reverse=True)[: self.max_visible]

    def deselect_all(self):
        with self._lock:
            self._selected = []

    def remove(self, rid):
        with self._lock:
            self._runs.pop(rid, None)
            if rid in self._selected:
                self._selected.remove(rid)

    def clear_all(self):
        with self._lock:
            self._runs.clear()
            self._selected = []
