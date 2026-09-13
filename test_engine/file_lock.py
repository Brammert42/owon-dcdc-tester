"""Nonblocking process leases using each OS's native file locking API."""

import os
import sys
import tempfile
from pathlib import Path


def lock_exclusive(handle):
    if sys.platform == 'win32':
        import msvcrt
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write('\0')
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(handle):
    if sys.platform == 'win32':
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)


def hardware_lock_path():
    if sys.platform == 'win32':
        return Path(tempfile.gettempdir()) / 'owon-dcdc-tester.hardware.lock'
    # Preserve the Linux lease path, including compatibility with running older versions.
    return Path('/tmp') / f'owon-dcdc-tester-{os.getuid()}.hardware.lock'
