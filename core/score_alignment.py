from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mido

from core.lilypond_intent import LilyPondScoreIntent
from core.pdf_score_intelligence import PdfScoreAnalysis, analyze_pdf_score


@dataclass(frozen=True)
class ScoreMidiFitReport:
    score_note_count: int
    midi_note_count: int
    ratio: float | None
    status: str
    summary: str
    detail: str


def compare_lilypond_to_midi(intent: LilyPondScoreIntent, midi_path: str | Path) -> ScoreMidiFitReport:
    midi_notes = _count_midi_note_on(midi_path)
    score_notes = max(0, int(intent.note_count))
    ratio: float | None = None
    status = "No fit estimate"
    if score_notes and midi_notes:
        ratio = midi_notes / score_notes
        if 0.85 <= ratio <= 1.15:
            status = "Likely compatible"
        elif 0.65 <= ratio <= 1.35:
            status = "Possibly compatible"
        else:
            status = "Needs alignment"
    elif score_notes and not midi_notes:
        status = "MIDI has no note-on events"
    elif midi_notes and not score_notes:
        status = "Score note count unavailable"
    summary = f"{status} · score {score_notes} notes vs MIDI {midi_notes} notes"
    if ratio is not None:
        detail = f"First-pass note-count ratio {ratio:.2f}. This is a rough fit check, not bar-by-bar alignment yet."
    else:
        detail = "First-pass fit check could not compute a useful ratio."
    if intent.repeat_blocks:
        detail += " LilyPond repeats may make note counts differ until repeat expansion is implemented."
    return ScoreMidiFitReport(score_notes, midi_notes, ratio, status, summary, detail)



def compare_pdf_to_midi(pdf_path: str | Path, midi_path: str | Path, page_range: tuple[int, int] | None = None) -> ScoreMidiFitReport:
    """Prototype score/MIDI match report for the PDF-first workflow.

    Without full OMR, a PDF cannot yet provide authoritative note counts. This
    report still connects the two user-selected sources and explains whether the
    PDF exposes enough embedded musical text to guide a take.
    """
    analysis = analyze_pdf_score(pdf_path, page_range=page_range)
    midi_notes = _count_midi_note_on(midi_path)
    score_notes = len(analysis.dynamic_sequence)
    if analysis.dynamic_sequence and midi_notes:
        status = "PDF guide ready"
        summary = f"PDF guide ready · {len(analysis.dynamic_sequence)} dynamic marks · {analysis.analyzed_pages_label} · MIDI {midi_notes} notes"
        detail = (
            "Prototype alignment maps the detected PDF dynamic sequence proportionally across MIDI note-on events. "
            "Full optical note alignment is still pending."
        )
        ratio = None
    elif analysis.has_guidance and midi_notes:
        status = "Partial PDF guide"
        summary = f"Partial PDF guide · musical text found · {analysis.analyzed_pages_label} · MIDI {midi_notes} notes"
        detail = "The PDF exposes tempo/articulation text, but no usable dynamic sequence for take generation yet."
        ratio = None
    elif midi_notes:
        status = "Needs OMR"
        summary = f"Needs OMR · PDF attached · {analysis.analyzed_pages_label} · MIDI {midi_notes} notes"
        detail = "This PDF did not expose embedded musical markings. A real OMR engine is needed before score-guided alignment can alter the MIDI."
        ratio = None
    else:
        status = "MIDI has no note-on events"
        summary = "MIDI has no note-on events"
        detail = "Score/MIDI comparison could not proceed because the loaded MIDI has no playable notes."
        ratio = None
    return ScoreMidiFitReport(score_notes, midi_notes, ratio, status, summary, detail)

def _count_midi_note_on(path: str | Path) -> int:
    midi = mido.MidiFile(path)
    return sum(1 for track in midi.tracks for msg in track if getattr(msg, "type", None) == "note_on" and getattr(msg, "velocity", 0) > 0)


def compare_score_reference_to_midi(reference, midi_path: str | Path) -> ScoreMidiFitReport:
    midi_notes = _count_midi_note_on(midi_path)
    score_notes = max(0, int(getattr(reference, "note_count", 0)))
    ratio: float | None = None
    status = "No fit estimate"
    if score_notes and midi_notes:
        ratio = midi_notes / score_notes
        if 0.75 <= ratio <= 1.25:
            status = "Likely same piece"
        elif 0.45 <= ratio <= 1.75:
            status = "Possible match"
        else:
            status = "Weak match"
    elif score_notes and not midi_notes:
        status = "MIDI has no note-on events"
    elif midi_notes and not score_notes:
        status = "OMR note count unavailable"
    summary = f"{status} · OMR score {score_notes} notes vs MIDI {midi_notes} notes"
    if ratio is not None:
        detail = f"First OMR/MIDI fit ratio {ratio:.2f}. Key/edition/repeat differences are not fully aligned yet."
    else:
        detail = "OMR/MIDI comparison could not compute a useful ratio."
    return ScoreMidiFitReport(score_notes, midi_notes, ratio, status, summary, detail)
