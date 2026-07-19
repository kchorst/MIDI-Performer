from pathlib import Path


def _source() -> str:
    return (Path(__file__).parents[1] / "app" / "main_window.py").read_text(encoding="utf-8")


def test_first_run_setup_has_explicit_actions():
    source = _source()
    assert 'QPushButton("Install FluidSynth…")' in source
    assert 'QPushButton("Install GeneralUser GS…")' in source
    assert 'QPushButton("Use Existing…")' in source
    assert 'QPushButton("Set Up Audio…")' not in source
    assert 'class InstrumentLibraryManagerDialog' in source
    assert 'GeneralUser GS (Recommended)' in source


def test_first_run_playback_test_is_inline_and_has_feedback_actions():
    source = _source()
    assert 'self.audio_test_panel = QFrame()' in source
    assert 'QPushButton("Yes, I heard it")' in source
    assert 'QPushButton("No sound")' in source
    assert 'QPushButton("Play Again")' in source
    assert 'self.test_button.clicked.connect(self.start_audio_test)' in source
    assert 'self.parent_window.run_audio_diagnostics(self)' not in source[source.index("class FirstRunSetupDialog"):source.index("class SettingsDialog")]


def test_audio_test_does_not_block_before_playing_the_chord():
    source = _source()
    assert '"Audio route test",\n            "MIDI Performer will try a short piano chord' not in source
    perform_index = source.index("def _perform_audio_test")
    tone_index = source.index("self.parent_window.playback_engine.test_audio_tone", perform_index)
    feedback_index = source.index("Did you hear the piano chord?", tone_index)
    assert tone_index < feedback_index
