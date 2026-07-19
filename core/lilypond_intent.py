from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re


LILYPOND_DYNAMICS_ORDER = ["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff", "sfz", "fp"]
DYNAMIC_SET = set(LILYPOND_DYNAMICS_ORDER)
MARKER_ARTICULATIONS = {
    "-.": "staccato",
    "--": "tenuto",
    "->": "accent",
    "-^": "marcato",
}
COMMAND_ARTICULATIONS = {
    "staccato": "staccato",
    "accent": "accent",
    "tenuto": "tenuto",
    "marcato": "marcato",
    "portato": "portato",
    "fermata": "fermata",
    "trill": "trill",
}
TITLE_RE = re.compile(r"title\s*=\s*\"([^\"]+)\"")
TEMPO_RE = re.compile(
    r"\\tempo\s+(?:\"([^\"]+)\"\s*)?(?:(\d+)\s*=?\s*(\d+))?",
    re.IGNORECASE,
)
NOTE_RE = re.compile(
    r"(?<![A-Za-z\\])([a-g](?:isis|eses|is|es)?[,']*)([!?]?)(\d+)?(\.*)(?![A-Za-z])",
    re.IGNORECASE,
)
REST_RE = re.compile(r"(?<![A-Za-z\\])[rs](\d+)?\.*(?![A-Za-z])", re.IGNORECASE)


@dataclass(frozen=True)
class LilyPondMeasureIntent:
    number: int
    note_count: int = 0
    rest_count: int = 0
    dynamics: list[str] = field(default_factory=list)
    articulations: list[str] = field(default_factory=list)
    tempo_marks: list[str] = field(default_factory=list)
    slur_starts: int = 0
    slur_ends: int = 0

    def has_expression(self) -> bool:
        return bool(self.dynamics or self.articulations or self.tempo_marks or self.slur_starts or self.slur_ends)


@dataclass(frozen=True)
class LilyPondScoreIntent:
    path: str
    title: str | None
    measures: list[LilyPondMeasureIntent]
    note_count: int
    rest_count: int
    dynamics: list[str]
    articulations: list[str]
    tempo_marks: list[str]
    repeat_blocks: int
    volta_repeats: int
    slur_count: int
    phrase_slur_count: int
    warnings: list[str] = field(default_factory=list)

    @property
    def measure_count(self) -> int:
        return len(self.measures)

    @property
    def expressive_measure_count(self) -> int:
        return sum(1 for measure in self.measures if measure.has_expression())

    def compact_text(self) -> str:
        bits: list[str] = []
        if self.title:
            bits.append(self.title)
        bits.append(f"{self.measure_count} measure{'s' if self.measure_count != 1 else ''}")
        bits.append(f"{self.note_count} note tokens")
        if self.dynamics:
            bits.append("dyn " + "/".join(self.dynamics[:5]))
        if self.articulations:
            bits.append("art " + ", ".join(self.articulations[:4]))
        if self.repeat_blocks:
            bits.append(f"{self.repeat_blocks} repeat block{'s' if self.repeat_blocks != 1 else ''}")
        return " · ".join(bits)

    def detail_text(self) -> str:
        parts: list[str] = []
        if self.title:
            parts.append(f"title: {self.title}")
        parts.append(f"measures: {self.measure_count}")
        parts.append(f"notes/rests: {self.note_count}/{self.rest_count}")
        if self.dynamics:
            parts.append("dynamics: " + ", ".join(self.dynamics))
        if self.articulations:
            parts.append("articulations: " + ", ".join(self.articulations))
        if self.tempo_marks:
            parts.append("tempo: " + ", ".join(self.tempo_marks[:8]))
        if self.repeat_blocks:
            parts.append(f"repeats: {self.repeat_blocks} total, {self.volta_repeats} volta")
        if self.slur_count or self.phrase_slur_count:
            parts.append(f"slurs: {self.slur_count}; phrase slurs: {self.phrase_slur_count}")
        if self.expressive_measure_count:
            parts.append(f"expressive measures: {self.expressive_measure_count}")
        if self.warnings:
            parts.append("warnings: " + "; ".join(self.warnings[:3]))
        return "; ".join(parts)

    def performance_hint_text(self) -> str:
        hints: list[str] = []
        if self.dynamics:
            hints.append("dynamic shaping candidates")
        if self.articulations:
            hints.append("articulation/touch candidates")
        if self.tempo_marks:
            hints.append("tempo/character candidates")
        if self.repeat_blocks:
            hints.append("repeat/section variation candidates")
        if self.slur_count or self.phrase_slur_count:
            hints.append("phrase-shaping candidates")
        return ", ".join(hints) if hints else "basic note/bar structure only"


def parse_lilypond_file(path: str | Path) -> LilyPondScoreIntent:
    source = Path(path)
    text = source.read_text(encoding="utf-8", errors="ignore")
    return parse_lilypond_text(text, path=str(source))


def parse_lilypond_text(text: str, path: str = "") -> LilyPondScoreIntent:
    stripped = strip_lilypond_comments(text)
    title_match = TITLE_RE.search(stripped)
    title = title_match.group(1).strip() if title_match else None
    tempo_marks = _find_tempo_marks(stripped)
    repeat_blocks = len(re.findall(r"\\repeat\b", stripped))
    volta_repeats = len(re.findall(r"\\repeat\s+volta\b", stripped))
    phrase_slur_count = stripped.count(r"\(")
    music_text = _prepare_music_text_for_token_scan(stripped)
    segments = _split_lilypond_measures(music_text)
    measures: list[LilyPondMeasureIntent] = []
    for idx, segment in enumerate(segments, start=1):
        notes = _count_notes(segment)
        rests = len(REST_RE.findall(segment))
        if notes == 0 and rests == 0 and not _measure_has_non_note_intent(segment):
            continue
        measures.append(
            LilyPondMeasureIntent(
                number=len(measures) + 1,
                note_count=notes,
                rest_count=rests,
                dynamics=_find_dynamics(segment),
                articulations=_find_articulations(segment),
                tempo_marks=_find_tempo_marks(segment),
                slur_starts=_count_slur_starts(segment),
                slur_ends=_count_slur_ends(segment),
            )
        )
    if not measures:
        notes = _count_notes(music_text)
        rests = len(REST_RE.findall(music_text))
        measures = [
            LilyPondMeasureIntent(
                number=1,
                note_count=notes,
                rest_count=rests,
                dynamics=_find_dynamics(music_text),
                articulations=_find_articulations(music_text),
                tempo_marks=tempo_marks,
                slur_starts=_count_slur_starts(music_text),
                slur_ends=_count_slur_ends(music_text),
            )
        ]
    dynamics = _ordered_unique([dyn for measure in measures for dyn in measure.dynamics], LILYPOND_DYNAMICS_ORDER)
    articulations = sorted({art for measure in measures for art in measure.articulations})
    all_tempo_marks = _ordered_unique(tempo_marks + [mark for measure in measures for mark in measure.tempo_marks])
    warnings: list[str] = []
    if "\\relative" in stripped:
        warnings.append("relative pitch anchor ignored for note counting")
    if "\\include" in stripped:
        warnings.append("included LilyPond files are not expanded yet")
    if "<<" in stripped or "\\new Staff" in stripped or "\\new Voice" in stripped:
        warnings.append("multi-staff/voice source is summarized, not fully aligned yet")
    return LilyPondScoreIntent(
        path=path,
        title=title,
        measures=measures,
        note_count=sum(m.note_count for m in measures),
        rest_count=sum(m.rest_count for m in measures),
        dynamics=dynamics,
        articulations=articulations,
        tempo_marks=all_tempo_marks,
        repeat_blocks=repeat_blocks,
        volta_repeats=volta_repeats,
        slur_count=sum(m.slur_starts for m in measures),
        phrase_slur_count=phrase_slur_count,
        warnings=warnings,
    )


def strip_lilypond_comments(text: str) -> str:
    text = re.sub(r"%\{.*?%\}", " ", text, flags=re.DOTALL)
    return "\n".join(line.split("%", 1)[0] for line in text.splitlines())


def _prepare_music_text_for_token_scan(text: str) -> str:
    text = re.sub(r"\\relative\s+[a-g](?:isis|eses|is|es)?[,']*", r"\\relative", text, flags=re.IGNORECASE)
    text = re.sub(r"\\key\s+[a-g](?:isis|eses|is|es)?[,']*\s+\\[A-Za-z]+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\\clef\s+(?:\"[^\"]+\"|[A-Za-z]+)", " ", text)
    text = re.sub(r"\\time\s+\d+\s*/\s*\d+", " ", text)
    text = re.sub(r"\\version\s+\"[^\"]+\"", " ", text)
    text = re.sub(r"\\header\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", " ", text, flags=re.DOTALL)
    text = re.sub(r"\"[^\"]*\"", " ", text)
    return text


def _split_lilypond_measures(text: str) -> list[str]:
    # Bar tokens can be |, ||, |., :|, etc. The split is intentionally approximate;
    # it produces a useful first pass without requiring a full LilyPond grammar.
    parts = re.split(r"\|[|.:]*", text)
    return [part.strip() for part in parts if part.strip()]


def _count_notes(text: str) -> int:
    return len(NOTE_RE.findall(text))


def _find_dynamics(text: str) -> list[str]:
    found = []
    for dyn in LILYPOND_DYNAMICS_ORDER:
        if re.search(r"\\" + re.escape(dyn) + r"\b", text):
            found.append(dyn)
    return found


def _find_articulations(text: str) -> list[str]:
    found: set[str] = set()
    for marker, name in MARKER_ARTICULATIONS.items():
        if marker in text:
            found.add(name)
    for command, name in COMMAND_ARTICULATIONS.items():
        if re.search(r"\\" + re.escape(command) + r"\b", text):
            found.add(name)
    return sorted(found)


def _find_tempo_marks(text: str) -> list[str]:
    marks: list[str] = []
    for match in TEMPO_RE.finditer(text):
        word = (match.group(1) or "").strip()
        beat = match.group(2)
        bpm = match.group(3)
        if word and beat and bpm:
            marks.append(f"{word} {beat}={bpm}")
        elif beat and bpm:
            marks.append(f"{beat}={bpm}")
        elif word:
            marks.append(word)
        else:
            marks.append("tempo")
    return _ordered_unique(marks)


def _count_slur_starts(text: str) -> int:
    phrase_markers = text.count(r"\(")
    return max(0, text.count("(") - phrase_markers)


def _count_slur_ends(text: str) -> int:
    phrase_markers = text.count(r"\)")
    return max(0, text.count(")") - phrase_markers)


def _measure_has_non_note_intent(text: str) -> bool:
    return bool(_find_dynamics(text) or _find_articulations(text) or _find_tempo_marks(text) or r"\repeat" in text)


def _ordered_unique(values: list[str], preferred_order: list[str] | None = None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    if preferred_order:
        value_set = set(values)
        for item in preferred_order:
            if item in value_set and item not in seen:
                result.append(item)
                seen.add(item)
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
