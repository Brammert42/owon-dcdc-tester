"""Cross-platform discovery, leases and frozen settings; no instrument I/O."""

from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import app_paths
from instruments import discovery
from test_engine import config as cfg
from test_engine.file_lock import lock_exclusive, unlock
from test_engine.engine import TestEngine


class PortabilityTests(unittest.TestCase):
    def test_windows_ports_are_not_files_and_deduplicate(self):
        ports = [SimpleNamespace(device='com4'), SimpleNamespace(device='COM4'), SimpleNamespace(device='COM12')]
        with patch.object(discovery.sys, 'platform', 'win32'), \
             patch.object(discovery.list_ports, 'comports', return_value=ports), \
             patch('instruments.discovery.sorted', side_effect=lambda x: list(x), create=True), \
             patch.object(discovery.os.path, 'exists', return_value=False):
            self.assertEqual(discovery.scan_candidate_ports(), ['COM4', 'COM12'])
            self.assertTrue(discovery.port_available('\\\\.\\com12'))
            self.assertFalse(discovery.port_available('COM9'))
            self.assertEqual(discovery.port_key('com4'), discovery.port_key('COM4'))

    @unittest.skipIf(sys.platform == 'win32', 'Linux alias semantics')
    def test_linux_alias_preferred_and_deduplicated(self):
        with patch.object(discovery.list_ports, 'comports', return_value=[]), \
             patch.object(discovery.glob, 'glob', return_value=['/dev/ttyUSB0']), \
             patch.object(discovery.os.path, 'exists', side_effect=lambda p: p != '/dev/owon_load'), \
             patch.object(discovery.os.path, 'realpath', return_value='/dev/ttyUSB0'):
            self.assertEqual(discovery.scan_candidate_ports(), ['/dev/owon_psu'])

    def test_stale_saved_ports_are_rediscovered_on_reconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = cfg.load_config()
            c['serial_ports'] = {'psu': 'COM1', 'load': 'COM2'}
            engine = TestEngine(dry_run=False, config=c, storage_root=tmp)
            resources = [Mock(), Mock()]
            resources[0].query.return_value = 'OWON,SPE15054,PSU,1.0'
            resources[1].query.return_value = 'OWON,OEL1520,LOAD,1.0'
            with patch.object(engine, '_acquire_hardware'), \
                 patch.object(discovery, 'port_available', return_value=False), \
                 patch.object(discovery, 'scan_all', return_value=[
                     {'port': 'COM7', 'device_type': 'psu'}, {'port': 'COM8', 'device_type': 'load'}]), \
                 patch('test_engine.engine.SerialResource', side_effect=resources) as serial, \
                 patch('test_engine.engine.OwonPSU'), patch('test_engine.engine.OwonLoad'):
                engine.connect()
                self.assertEqual([call.args[0] for call in serial.call_args_list], ['COM7', 'COM8'])
                engine.disconnect()
            engine.close()

    def test_lease_blocks_another_process_then_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'lease'
            code = ('import sys; from test_engine.file_lock import lock_exclusive; '
                    'f=open(sys.argv[1],"a"); lock_exclusive(f)')
            with path.open('a') as handle:
                lock_exclusive(handle)
                blocked = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True)
                self.assertNotEqual(blocked.returncode, 0)
                unlock(handle)
                available = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True)
                self.assertEqual(available.returncode, 0, available.stderr.decode())

    def test_frozen_windows_uses_local_app_data(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sys, 'frozen', True, create=True), \
             patch.object(sys, 'platform', 'win32'), patch.dict(os.environ, LOCALAPPDATA=tmp):
            self.assertEqual(app_paths.runtime_root(), Path(tmp) / 'OWON-Tester')

    def test_bundled_default_seed_does_not_overwrite_manager_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'user' / 'config.json'
            with patch.object(cfg, 'CONFIG_PATH', path):
                initial = cfg.load_config(path)
                initial['production_test_profile']['thermal_check']['max_temp_c'] = 76
                cfg.save_config(initial, path)
                again = cfg.load_config(path)
            self.assertEqual(again['production_test_profile']['thermal_check']['max_temp_c'], 76)
            self.assertEqual(json.loads(path.read_text())['production_test_profile']['thermal_check']['max_temp_c'], 76)
