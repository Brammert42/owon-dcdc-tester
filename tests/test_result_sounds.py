"""Cue dispatch and portable WAV assets, without playing sound during tests."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import wave
from desktop.result_sounds import ResultSounds


class SoundTests(unittest.TestCase):
    def test_cues_and_mute(self):
        sounds = ResultSounds.__new__(ResultSounds)
        sounds.engine = SimpleNamespace(_config={'result_sounds_enabled': True})
        sounds.effects = {'PASS': Mock(), 'FAIL': Mock()}
        sounds.play('PASS')
        sounds.effects['PASS'].play.assert_called_once()
        sounds.effects['FAIL'].play.assert_not_called()
        sounds.play('ABORTED')
        sounds.effects['FAIL'].play.assert_called_once()
        sounds.engine._config['result_sounds_enabled'] = False
        sounds.play('FAIL')
        sounds.effects['FAIL'].play.assert_called_once()
        sounds.play('FAIL', force=True)
        self.assertEqual(sounds.effects['FAIL'].play.call_count, 2)

    def test_wav_files_are_distinct_and_short(self):
        root = Path(__file__).resolve().parent.parent / 'static' / 'sounds'
        self.assertNotEqual((root / 'pass.wav').read_bytes(), (root / 'fail.wav').read_bytes())
        for name in ('pass.wav', 'fail.wav'):
            with wave.open(str(root / name)) as wav:
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getsampwidth(), 2)
                self.assertGreater(wav.getnframes() / wav.getframerate(), 0.3)
                self.assertLess(wav.getnframes() / wav.getframerate(), 2)
