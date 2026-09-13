#!/usr/bin/env python3
"""Compatibility test command: all tests use isolated temporary storage."""

import unittest

if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.discover("tests")
    )
    raise SystemExit(not result.wasSuccessful())
