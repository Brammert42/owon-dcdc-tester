"""Short, distinct local completion cues; audio failure never affects testing."""

from PySide6.QtCore import QUrl
from app_paths import resource_path


class ResultSounds:
    def __init__(self, engine, parent=None):
        self.engine = engine
        self.effects = {}
        try:
            from PySide6.QtMultimedia import QSoundEffect
            for result, name in [('PASS', 'pass.wav'), ('FAIL', 'fail.wav')]:
                effect = QSoundEffect(parent)
                effect.setSource(QUrl.fromLocalFile(str(resource_path('static', 'sounds', name))))
                effect.setVolume(0.65)
                self.effects[result] = effect
        except (ImportError, RuntimeError):
            pass

    def play(self, result, force=False):
        if not force and not self.engine._config.get('result_sounds_enabled', True):
            return
        key = 'PASS' if result == 'PASS' else 'FAIL'
        for effect in self.effects.values():
            effect.stop()
        if key in self.effects:
            self.effects[key].play()
