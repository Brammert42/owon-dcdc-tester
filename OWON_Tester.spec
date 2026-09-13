# Folder-based desktop bundle. Run PyInstaller on the target operating system.
from pathlib import Path
import sys

root = Path(SPECPATH)
assets = [
    (str(root / 'config.json'), '.'),
    (str(root / 'static' / 'logo.png'), 'static'),
    (str(root / 'static' / 'sounds' / 'pass.wav'), 'static/sounds'),
    (str(root / 'static' / 'sounds' / 'fail.wav'), 'static/sounds'),
]
a = Analysis(
    [str(root / 'run_desktop.py')],
    pathex=[str(root)],
    binaries=[],
    datas=assets,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={'matplotlib': {'backends': ['QtAgg']}},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='OWON_Tester',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(root / 'static' / 'logo.png') if sys.platform == 'win32' else None,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name='OWON-Tester-Windows' if sys.platform == 'win32' else 'OWON-Tester-Linux',
)
