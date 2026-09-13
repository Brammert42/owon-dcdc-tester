"""Durable per-session diagnostics; retained until engine shutdown."""

import csv
import datetime
from pathlib import Path
import threading
import uuid


class TransactionLog:
    def __init__(self, directory, label="desktop"):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.path = str(
            Path(directory)
            / f"session_{datetime.datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_{label}.csv"
        )
        self._file = open(self.path, "x", newline="", encoding='utf-8')
        self._writer = csv.writer(self._file)
        self._writer.writerow(["timestamp", "entry_type", "message"])
        self._lock = threading.Lock()
        self._file.flush()

    def write(self, kind, **fields):
        with self._lock:
            if self._file.closed:
                return
            self._writer.writerow(
                [datetime.datetime.now().astimezone().isoformat(), kind, str(fields)]
            )
            self._file.flush()

    def close(self):
        with self._lock:
            self._file.close()
