from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
import json
from typing import Optional

from core.version import APP_VERSION


@dataclass
class MidiPerformerProject:
    app_name: str = "MIDI Performer"
    app_version: str = APP_VERSION
    project_version: int = 36
    source_midi_path: Optional[str] = None
    selected_soundfont_path: Optional[str] = None
    instrument_family: str = "Loaded instruments"
    instrument_name: str = "Keep loaded track instruments"
    instrument_program: Optional[int] = None
    output_directory: Optional[str] = None
    project_directory: Optional[str] = None
    current_project_path: Optional[str] = None
    score_source_path: Optional[str] = None
    score_source_format: Optional[str] = None
    score_source_status: Optional[str] = None
    score_source_summary: Optional[str] = None
    score_source_detail: Optional[str] = None
    score_intent_summary: Optional[str] = None
    score_intent_detail: Optional[str] = None
    score_alignment_summary: Optional[str] = None
    score_alignment_detail: Optional[str] = None
    score_analysis_status: Optional[str] = None
    score_analysis_summary: Optional[str] = None
    score_analysis_detail: Optional[str] = None
    score_analysis_details_summary: Optional[str] = None
    score_analysis_details_detail: Optional[str] = None
    score_alignment_confidence: Optional[str] = None
    score_alignment_repair_needed: bool = False
    score_alignment_shift_percent: float = 0.0
    score_alignment_stretch_percent: float = 100.0
    score_alignment_shift_beats: float = 0.0
    score_alignment_length_beats: float = 0.0
    score_alignment_repair_summary: Optional[str] = None
    score_guide_available: bool = False
    score_omr_engine_status: Optional[str] = None
    score_omr_engine_detail: Optional[str] = None
    score_omr_output_path: Optional[str] = None
    score_omr_summary: Optional[str] = None
    score_omr_detail: Optional[str] = None
    score_omr_dynamic_sequence: list[str] = field(default_factory=list)
    score_omr_articulation_sequence: list[str] = field(default_factory=list)
    score_omr_tempo_words: list[str] = field(default_factory=list)
    score_omr_fermata_words: list[str] = field(default_factory=list)
    score_focus_mode: str = "all"
    score_focus_start_page: Optional[int] = None
    score_focus_end_page: Optional[int] = None
    score_focus_page_count: Optional[int] = None
    score_focus_summary: Optional[str] = None
    score_guidance_enabled: bool = False
    score_dynamics_enabled: bool = False
    score_guidance_strength: str = "Light"
    reference_audio_path: Optional[str] = None
    reference_audio_format: Optional[str] = None
    reference_audio_summary: Optional[str] = None
    reference_audio_detail: Optional[str] = None
    reference_audio_duration_seconds: Optional[float] = None
    reference_audio_analysis_summary: Optional[str] = None
    reference_audio_analysis_detail: Optional[str] = None
    reference_audio_peak_percent: Optional[int] = None
    reference_audio_rms_percent: Optional[int] = None
    reference_audio_loudness_curve: list[int] = field(default_factory=list)
    reference_audio_waveform_curve: list[int] = field(default_factory=list)
    reference_audio_attack_curve: list[int] = field(default_factory=list)
    reference_audio_motion_curve: list[int] = field(default_factory=list)
    reference_audio_alignment_shift_percent: float = 0.0
    reference_audio_alignment_stretch_percent: float = 100.0
    reference_audio_alignment_shift_beats: float = 0.0
    reference_audio_alignment_length_beats: float = 0.0
    reference_audio_guidance_enabled: bool = False
    reference_audio_guidance_strength: str = "Light"
    alignment_preview_start_bar: int = 0
    alignment_preview_window_bars: int = 4
    master_volume: int = 100
    playback_tempo_percent: int = 100
    expressiveness: int = 0
    timing_feel: str = "Natural"
    versions: dict[str, Optional[str]] = field(default_factory=lambda: {
        "minimal": None,
        "moderate": None,
        "enhanced": None,
    })
    undo_versions: dict[str, Optional[str]] = field(default_factory=lambda: {
        "minimal": None,
        "moderate": None,
        "enhanced": None,
    })
    version_notes: dict[str, str] = field(default_factory=lambda: {
        "minimal": "",
        "moderate": "",
        "enhanced": "",
    })
    version_parameter_reports: dict[str, dict] = field(default_factory=dict)
    track_program_overrides: dict[str, Optional[int]] = field(default_factory=dict)
    track_volume_overrides: dict[str, int] = field(default_factory=dict)
    track_pan_overrides: dict[str, int] = field(default_factory=dict)
    track_octave_overrides: dict[str, int] = field(default_factory=dict)
    track_solo_overrides: dict[str, bool] = field(default_factory=dict)
    track_mute_overrides: dict[str, bool] = field(default_factory=dict)
    playback_start_percent: int = 0
    performance_preset: str = "Balanced"
    takes: list[dict] = field(default_factory=list)
    candidate_take: Optional[dict] = None
    audition_counter: int = 0

    def reset_versions(self) -> None:
        self.versions = {"minimal": None, "moderate": None, "enhanced": None}
        self.undo_versions = {"minimal": None, "moderate": None, "enhanced": None}
        self.version_notes = {"minimal": "", "moderate": "", "enhanced": ""}
        self.version_parameter_reports = {}
        self.takes = []
        self.candidate_take = None
        self.audition_counter = 0

    @classmethod
    def load(cls, path: str | Path) -> "MidiPerformerProject":
        source = Path(path)
        data = json.loads(source.read_text(encoding="utf-8"))
        project = cls()
        valid_fields = set(project.__dataclass_fields__.keys())
        for key, value in data.items():
            if key in valid_fields:
                setattr(project, key, value)
        # Score and reference-audio alignment are stored separately. Older projects used
        # the score alignment values for both guides, so preserve that behavior
        # on first load instead of silently resetting the audio guide.
        if "reference_audio_alignment_shift_percent" not in data and project.reference_audio_path:
            project.reference_audio_alignment_shift_percent = float(project.score_alignment_shift_percent or 0.0)
            project.reference_audio_alignment_stretch_percent = float(project.score_alignment_stretch_percent or 100.0)
            project.reference_audio_alignment_shift_beats = float(project.score_alignment_shift_beats or 0.0)
            project.reference_audio_alignment_length_beats = float(project.score_alignment_length_beats or 0.0)
        project.project_version = max(36, int(getattr(project, "project_version", 36) or 36))
        return project

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
