from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_SETTINGS = {
    "last_soundfont_path": None,
    "last_midi_path": None,
    "last_midi_directory": None,
    "last_output_directory": None,
    "last_project_directory": None,
    "last_soundfont_directory": None,
    "master_volume": 100,
    "playback_tempo_percent": 100,
    "recent_midi_paths": [],
    "recent_soundfont_paths": [],
    "recent_project_paths": [],
    "recent_score_source_paths": [],
    "recent_reference_audio_paths": [],
    "last_score_source_directory": None,
    "last_reference_audio_directory": None,
    "last_export_kind": "wav",
    "audiveris_path": None,
    "fluidsynth_path": None,
    "skip_delete_unchecked_confirm": False,
    "audio_driver": "default",
    "audio_driver_verified": False,
}



class AppSettings:
    def __init__(self, app_root: str | Path) -> None:
        self.app_root = Path(app_root)
        self.path = self.app_root / "config" / "app_settings.json"
        self.data: dict[str, Any] = copy.deepcopy(DEFAULT_SETTINGS)
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except Exception:
            # Settings should never stop the app from launching.
            self.data = copy.deepcopy(DEFAULT_SETTINGS)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()
