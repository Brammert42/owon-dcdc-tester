"""Queued Qt delivery and nonblocking hardware tasks."""

from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, Signal, Qt


class Controller(QObject):
    event = Signal(str, object)
    completed = Signal(int, object, object)
    busy_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="desktop-command"
        )
        self.safety_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="desktop-stop"
        )
        self._callbacks = {}
        self._next = 0
        self._pending = 0
        self.completed.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        for event in [
            "status_update",
            "measurement",
            "prod_step",
            "prod_result",
            "thermal_check",
            "clear_run",
            "sweep_done",
            "sweep_error",
            "graph_data_changed",
            "emergency_stop",
        ]:
            engine.on(event, lambda *args, e=event: self.event.emit(e, args))

    @property
    def pending(self):
        return self._pending > 0

    @property
    def foreground_pending(self):
        return any(not values[2] for values in self._callbacks.values())

    def submit(
        self,
        fn,
        *args,
        success=None,
        failure=None,
        safety=False,
        background=False,
        **kwargs,
    ):
        self._next += 1
        token = self._next
        self._callbacks[token] = (success, failure, background)
        self._pending += 1
        if not background:
            self.busy_changed.emit(True)

        def work():
            try:
                self.completed.emit(token, fn(*args, **kwargs), None)
            except Exception as exc:
                self.completed.emit(token, None, str(exc))

        (self.safety_pool if safety else self.pool).submit(work)

    def _deliver(self, token, result, error):
        success, failure, background = self._callbacks.pop(token, (None, None, False))
        self._pending -= 1
        if not background:
            self.busy_changed.emit(self.pending)
        if error is not None:
            if failure:
                failure(error)
            else:
                self.error.emit(error)
        elif success:
            success(result)

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.safety_pool.shutdown(wait=False, cancel_futures=True)
