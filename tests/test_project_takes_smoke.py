from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.project_model import MidiPerformerProject


def main() -> None:
    project = MidiPerformerProject()
    project.source_midi_path = "song.mid"
    project.candidate_take = {"id": "candidate", "name": "Preview", "path": "preview.mid", "note": "Light"}
    project.takes.append({"id": "take_0", "name": "Take 1", "path": "take1.mid", "note": "Light"})
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "test.emiproj"
        project.save(target)
        loaded = MidiPerformerProject.load(target)
    assert loaded.takes[0]["name"] == "Take 1"
    assert loaded.candidate_take["name"] == "Preview"
    loaded.reset_versions()
    assert loaded.takes == []
    assert loaded.candidate_take is None
    print("project_takes_smoke PASS")


if __name__ == "__main__":
    main()
