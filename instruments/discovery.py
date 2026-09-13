"""Identify exact OWON models; deduplicate device aliases and close every probe."""

import glob
import os
import re
import sys
from serial.tools import list_ports
from instruments.serial_resource import SerialResource
from instruments.drivers import hardware_lock


def _classify(idn):
    fields = [f.strip().upper() for f in re.split("[,;]", idn)]
    if not fields or fields[0] != "OWON":
        return "unknown"
    if "SPE15054" in fields:
        return "psu"
    if "OEL1520" in fields:
        return "load"
    return "unknown"


def scan_candidate_ports():
    if sys.platform == 'win32':
        # COM names are device identifiers, not filesystem paths.
        choices = [p.device for p in sorted(list_ports.comports())]
        return list(dict.fromkeys(port_key(p) for p in choices))
    choices = ["/dev/owon_psu", "/dev/owon_load"]
    for pattern in [
        "/dev/serial/by-id/*",
        "/dev/serial/by-path/*",
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
    ]:
        choices.extend(sorted(glob.glob(pattern)))
    choices.extend(p.device for p in sorted(list_ports.comports()))
    seen, result = set(), []
    for path in choices:
        real = os.path.realpath(path)
        if os.path.exists(path) and real not in seen:
            seen.add(real)
            result.append(path)
    return result


def port_key(port):
    if sys.platform == 'win32':
        return str(port).removeprefix('\\\\.\\').upper()
    return os.path.realpath(port)


def port_available(port):
    if sys.platform == 'win32':
        return port_key(port) in {port_key(p.device) for p in list_ports.comports()}
    return os.path.exists(port)


def probe_port(port, baudrate=115200, timeout=2):
    result = dict(
        port=port,
        real_port=port_key(port),
        idn=None,
        device_type="error",
        error=None,
    )
    resource = SerialResource(port, baudrate, timeout)
    with hardware_lock:
        try:
            resource.open()
            # Identification only. Do not change operating mode on arbitrary serial devices.
            idn = resource.query("*IDN?")
            result.update(idn=idn, device_type=_classify(idn))
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            resource.close()
    return result


def scan_all(baudrate=115200, timeout=2):
    return [probe_port(p, baudrate, timeout) for p in scan_candidate_ports()]
