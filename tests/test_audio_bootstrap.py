from pathlib import Path
import zipfile

import core.audio_bootstrap as setup


def test_audio_setup_uses_app_local_files(tmp_path, monkeypatch):
    fixture = tmp_path / "fixture.zip"
    with zipfile.ZipFile(fixture, "w") as z:
        z.writestr("fluidsynth/bin/fluidsynth.exe", b"MZ" + b"x" * 2048)
        z.writestr("fluidsynth/bin/helper.dll", b"dll")

    monkeypatch.setattr(setup, "_latest_windows_asset", lambda: ("fixture.zip", "https://example.invalid/fs.zip"))

    def fake_download(url, target, progress=None):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".zip":
            target.write_bytes(fixture.read_bytes())
        else:
            target.write_bytes(b"soundfont" * 256)
        if progress:
            progress(target.name, 100)

    monkeypatch.setattr(setup, "_download", fake_download)
    root = tmp_path / "app"
    result = setup.install_audio_dependencies(root)
    assert result.ok, result.detail
    assert Path(result.fluidsynth_path).is_file()
    assert Path(result.soundfont_path).is_file()
    assert root in Path(result.fluidsynth_path).parents
    assert root in Path(result.soundfont_path).parents
