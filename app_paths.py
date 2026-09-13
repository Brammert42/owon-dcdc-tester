"""Separate bundled read-only assets from writable per-user application data."""

import os
from pathlib import Path
import sys

RESOURCE_DIR = Path(__file__).resolve().parent


def runtime_root():
    if not getattr(sys, 'frozen', False):
        return RESOURCE_DIR
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'OWON-Tester'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'owon-dcdc-tester'


def resource_path(*parts):
    return RESOURCE_DIR.joinpath(*parts)
