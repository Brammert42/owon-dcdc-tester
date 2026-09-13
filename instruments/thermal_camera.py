"""THOR002 USB H.264 snapshot capture (rendered image, not radiometry)."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4


def find_camera(sys_root=Path('/sys/class/video4linux')):
    candidates = []
    for entry in sorted(Path(sys_root).glob('video*')):
        try:
            if 'thor' not in (entry / 'name').read_text().lower():
                continue
            if (entry / 'index').read_text().strip() != '0':
                continue
            candidates.append('/dev/' + entry.name)
        except OSError:
            continue
    if len(candidates) != 1:
        raise RuntimeError('Connect exactly one THOR camera with USB video enabled.')
    return candidates[0]


def capture_snapshot(directory, pcb_label='', operator=''):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('USB thermal snapshots currently require Linux. The operator-entered thermal check is available on Windows.')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('Thermal capture requires ffmpeg to be installed.')
    device = find_camera()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    safe_label = re.sub(r'[^A-Za-z0-9_.-]+', '_', pcb_label).strip('._')[:80] or 'unlabelled'
    stem = f'{now:%Y%m%dT%H%M%S}_{safe_label}_{uuid4().hex[:8]}'
    destination = directory / (stem + '.png')
    temporary = directory / (stem + '.partial.png')
    try:
        result = subprocess.run(
            [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
             '-f', 'v4l2', '-input_format', 'h264', '-video_size', '640x480',
             '-i', device, '-frames:v', '1', '-update', '1', str(temporary)],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode or not temporary.is_file() or not temporary.stat().st_size:
            raise RuntimeError('Thermal capture failed: ' + (result.stderr.strip()[-1200:] or 'No image received.'))
        temporary.replace(destination)
        destination.with_suffix('.json').write_text(json.dumps({
            'captured_at': now.isoformat(), 'pcb_label': pcb_label,
            'operator': operator, 'device': device, 'camera': 'THOR002',
            'image': destination.name, 'numeric_temperatures': False,
        }, indent=2) + '\n')
        return str(destination)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('THOR camera did not deliver an image within 15 seconds.') from exc
    finally:
        temporary.unlink(missing_ok=True)
