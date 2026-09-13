"""Exclusive, bounded serial transport. A lost connection is never replayed automatically."""

import serial
import time


class SerialResource:
    def __init__(self, port, baudrate=115200, timeout=2.0, terminator="\n", log=None):
        self._port, self._baudrate, self._timeout = port, baudrate, timeout
        self._terminator, self._ser, self.log = terminator, None, log

    def open(self):
        if not self.is_open:
            self._ser = serial.Serial(
                port=self._port,
                baudrate=self._baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self._timeout,
                write_timeout=self._timeout,
                exclusive=True,
            )

    @property
    def is_open(self):
        return self._ser is not None and self._ser.is_open

    def close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def _ensure_open(self):
        if not self.is_open:
            raise RuntimeError(f"Serial port {self._port} is closed")

    def write(self, cmd):
        self._ensure_open()
        payload = (cmd + self._terminator).encode("ascii")
        if self._ser.write(payload) != len(payload):
            raise serial.SerialTimeoutException("Incomplete serial write")
        # Do not use tcdrain/flush: it can wait indefinitely on a broken USB adapter.
        if self.log:
            self.log("TX", self._port, cmd)

    def query(self, cmd):
        self._ensure_open()
        self._ser.reset_input_buffer()
        self.write(cmd)
        time.sleep(0.05)
        raw = self._ser.read_until(b"\n", size=4096)
        if not raw.endswith(b"\n"):
            raise TimeoutError(f"{self._port}: incomplete response to {cmd}")
        response = raw.decode("ascii", errors="strict").strip()
        if self.log:
            self.log("RX", self._port, response)
        return response
