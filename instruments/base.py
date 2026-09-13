"""Compatibility imports; application code uses instruments.drivers."""

from .drivers import DriverError as InstrumentError, InstrumentDriver as InstrumentBase

__all__ = ["InstrumentError", "InstrumentBase"]
