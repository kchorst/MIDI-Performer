from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from core.lilypond_intent import parse_lilypond_file
from core.pdf_score_intelligence import analyze_pdf_score


@dataclass(frozen=True)
class ScoreMeasure:
    number: str
    note_count: int
    dynamics: list[str] = field(default_factory=list)
    articulations: list[str] = field(default_factory=list)
    tempo_words: list[str] = field(default_factory=list)
    fermatas: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScoreReference:
    path: str
    format: str
    measures: list[ScoreMeasure]

    @property
    def measure_count(self) -> int:
        return len(self.measures)

    @property
    def note_count(self) -> int:
        return sum(m.note_count for m in self.measures)

    def compact_text(self) -> str:
        return f"{self.format} · {self.measure_count} measures · {self.note_count} score notes"


@dataclass(frozen=True)
class ScoreSourceInfo:
    """Score-source metadata for intake and early LilyPond intent reporting.

    This remains non-destructive: score sources can be parsed and summarized, but they do not alter playback or Takes until score guidance is explicitly enabled for a new Take.
    """

    path: str
    format: str
    status: str
    summary: str
    detail: str = ""
    title: str | None = None
    measure_count: int | None = None
    note_count: int | None = None
    dynamics: list[str] = field(default_factory=list)
    articulations: list[str] = field(default_factory=list)
    tempo_words: list[str] = field(default_factory=list)
    fermatas: list[str] = field(default_factory=list)
    repeats: int | None = None
    page_count: int | None = None
    intent_summary: str | None = None
    intent_detail: str | None = None
    alignment_summary: str | None = None
    alignment_detail: str | None = None

    def compact_text(self) -> str:
        name = Path(self.path).name
        return f"{self.format} · {name} · {self.summary}"

    def as_project_fields(self) -> dict[str, object]:
        return {
            "score_source_path": self.path,
            "score_source_format": self.format,
            "score_source_status": self.status,
            "score_source_summary": self.summary,
            "score_source_detail": self.detail,
        }


SUPPORTED_SCORE_SUFFIXES = {".ly", ".ily", ".musicxml", ".xml", ".mxl", ".pdf"}
LILYPOND_DYNAMICS = ["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff", "sfz", "fp"]
LILYPOND_ARTICULATION_NAMES = {
    "-.": "staccato",
    "--": "tenuto",
    "->": "accent",
    "-^": "marcato",
}
TEMPO_WORD_RE = re.compile(r"\\tempo\s+(?:\"([^\"]+)\"|([A-Za-z][A-Za-z\s]+))?")
TITLE_RE = re.compile(r"title\s*=\s*\"([^\"]+)\"")
NOTE_TOKEN_RE = re.compile(r"(?<![A-Za-z])([a-g](?:is|es|isis|eses)?[,']*)(?:\d+)?", re.IGNORECASE)


def read_musicxml(path: str | Path) -> ScoreReference:
    """Read enough MusicXML structure for future score-informed interpretation.

    This parser does not itself perform bar-by-bar score-to-MIDI alignment. It establishes
    the intake seam: measures, basic note counts, dynamics, articulations, and tempo words.
    Both .musicxml/.xml and compressed .mxl containers are accepted.
    """
    source = Path(path)
    if source.suffix.lower() == ".mxl":
        with tempfile.TemporaryDirectory() as tmpdir:
            extracted = _extract_mxl_score_xml(source, Path(tmpdir))
            return _read_musicxml_tree(extracted, source)
    return _read_musicxml_tree(source, source)


def _extract_mxl_score_xml(source: Path, target_dir: Path) -> Path:
    with zipfile.ZipFile(source, "r") as zf:
        names = zf.namelist()
        candidates = [n for n in names if n.lower().endswith((".musicxml", ".xml")) and not n.lower().endswith("container.xml")]
        if not candidates:
            raise ValueError("Compressed MXL did not contain a MusicXML score file.")
        preferred = next((n for n in candidates if "score" in Path(n).name.lower()), candidates[0])
        zf.extract(preferred, target_dir)
        return target_dir / preferred


def _read_musicxml_tree(xml_path: Path, display_path: Path) -> ScoreReference:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    ns_prefix = ""
    if root.tag.startswith("{"):
        ns_prefix = root.tag.split("}", 1)[0] + "}"

    measures: list[ScoreMeasure] = []
    for measure in root.iter(f"{ns_prefix}measure"):
        number = str(measure.attrib.get("number", len(measures) + 1))
        notes = 0
        dynamics: set[str] = set()
        articulations: set[str] = set()
        tempo_words: list[str] = []
        fermatas: list[str] = []
        for note in measure.iter(f"{ns_prefix}note"):
            if note.find(f"{ns_prefix}rest") is None and note.find(f"{ns_prefix}pitch") is not None:
                notes += 1
            for dyn in note.iter(f"{ns_prefix}dynamics"):
                for child in list(dyn):
                    dynamics.add(child.tag.replace(ns_prefix, ""))
            for art in note.iter(f"{ns_prefix}articulations"):
                for child in list(art):
                    articulations.add(child.tag.replace(ns_prefix, ""))
            for fermata in note.iter(f"{ns_prefix}fermata"):
                fermatas.append((fermata.text or "fermata").strip() or "fermata")
        for direction in measure.iter(f"{ns_prefix}direction"):
            for dyn in direction.iter(f"{ns_prefix}dynamics"):
                for child in list(dyn):
                    dynamics.add(child.tag.replace(ns_prefix, ""))
            for words in direction.iter(f"{ns_prefix}words"):
                if words.text:
                    tempo_words.append(words.text.strip())
        measures.append(ScoreMeasure(number, notes, sorted(dynamics), sorted(articulations), tempo_words, fermatas))
    return ScoreReference(str(display_path), "MusicXML" if display_path.suffix.lower() != ".mxl" else "MXL", measures)


def read_score_source(path: str | Path) -> ScoreSourceInfo:
    source = Path(path)
    suffix = source.suffix.lower()
    if not source.exists():
        raise FileNotFoundError(str(source))
    if suffix not in SUPPORTED_SCORE_SUFFIXES:
        raise ValueError(f"Unsupported score source: {source.suffix or 'no extension'}")
    if suffix in {".musicxml", ".xml", ".mxl"}:
        return _read_musicxml_info(source)
    if suffix in {".ly", ".ily"}:
        return read_lilypond_source(source)
    if suffix == ".pdf":
        return read_pdf_score_source(source)
    raise ValueError(f"Unsupported score source: {source.suffix}")


def _read_musicxml_info(source: Path) -> ScoreSourceInfo:
    reference = read_musicxml(source)
    dynamics = sorted({d for measure in reference.measures for d in measure.dynamics})
    articulations = sorted({a for measure in reference.measures for a in measure.articulations})
    tempo_words = [w for measure in reference.measures for w in measure.tempo_words]
    fermatas = [f for measure in reference.measures for f in measure.fermatas]
    summary = f"structured · {reference.measure_count} measures · {reference.note_count} score notes"
    detail_parts = []
    if dynamics:
        detail_parts.append("dynamics: " + ", ".join(dynamics[:8]))
    if articulations:
        detail_parts.append("articulations: " + ", ".join(articulations[:8]))
    if tempo_words:
        detail_parts.append("tempo text: " + ", ".join(tempo_words[:4]))
    if fermatas:
        detail_parts.append("fermatas: " + ", ".join(fermatas[:4]))
    return ScoreSourceInfo(
        path=str(source),
        format=reference.format,
        status="Structured score loaded. Analyze it and enable Use score for a new Take.",
        summary=summary,
        detail="; ".join(detail_parts) or "MusicXML structure detected.",
        measure_count=reference.measure_count,
        note_count=reference.note_count,
        dynamics=dynamics,
        articulations=articulations,
        tempo_words=tempo_words[:12],
        fermatas=fermatas[:12],
    )


def read_lilypond_source(path: str | Path) -> ScoreSourceInfo:
    source = Path(path)
    intent = parse_lilypond_file(source)
    summary = "parsed LilyPond intent · " + intent.compact_text()
    detail_parts = [intent.detail_text(), "performance hints: " + intent.performance_hint_text()]
    detail = "; ".join(part for part in detail_parts if part)
    return ScoreSourceInfo(
        path=str(source),
        format="LilyPond",
        status="LilyPond score parsed. Optional score dynamics can guide new Takes.",
        summary=summary,
        detail=detail,
        title=intent.title,
        measure_count=intent.measure_count,
        note_count=intent.note_count,
        dynamics=intent.dynamics,
        articulations=intent.articulations,
        tempo_words=intent.tempo_marks[:12],
        repeats=intent.repeat_blocks,
        intent_summary=intent.compact_text(),
        intent_detail=detail,
    )

def _strip_lilypond_comments(text: str) -> str:
    # Remove line comments and common block comments. Good enough for intake summary;
    # the LilyPond parser can become more formal.
    text = re.sub(r"%\{.*?%\}", " ", text, flags=re.DOTALL)
    return "\n".join(line.split("%", 1)[0] for line in text.splitlines())


def _find_lilypond_dynamics(text: str) -> list[str]:
    found: list[str] = []
    for dyn in LILYPOND_DYNAMICS:
        if re.search(r"\\" + re.escape(dyn) + r"\b", text):
            found.append(dyn)
    # Preserve musical order from soft to loud; the list above is intentional.
    return found


def read_pdf_score_source(path: str | Path) -> ScoreSourceInfo:
    source = Path(path)
    analysis = analyze_pdf_score(source)
    detail_parts = [analysis.detail]
    if analysis.dynamic_sequence:
        detail_parts.append("dynamic sequence: " + ", ".join(analysis.dynamic_sequence[:16]))
    return ScoreSourceInfo(
        path=str(source),
        format="PDF",
        status=analysis.confidence,
        summary=analysis.summary,
        detail="; ".join(part for part in detail_parts if part),
        page_count=analysis.page_count,
        intent_summary=analysis.compact_text(),
        intent_detail="; ".join(part for part in detail_parts if part),
        dynamics=analysis.dynamics,
        articulations=analysis.articulation_words,
        tempo_words=analysis.tempo_words,
        fermatas=analysis.fermata_words,
    )


def _estimate_pdf_page_count(data: bytes) -> int | None:
    # Lightweight estimate only. It avoids pulling in PDF libraries for score prep.
    matches = re.findall(rb"/Type\s*/Page\b", data)
    return len(matches) or None


def score_reference_dynamic_sequence(reference: ScoreReference) -> list[str]:
    """Return a simple dynamic sequence from a structured score reference.

    OMR output is used internally. For this first bridge, we keep the mapping
    conservative: the latest dynamic found in each measure becomes that measure's
    guidance mark, then it is spread proportionally across the MIDI notes.
    """
    sequence: list[str] = []
    current: str | None = None
    for measure in reference.measures:
        if measure.dynamics:
            current = measure.dynamics[-1].lower()
        if current:
            sequence.append(current)
    return sequence


def score_reference_articulation_sequence(reference: ScoreReference) -> list[str]:
    """Return one articulation-style hint per measure where available.

    This keeps OMR-derived score guidance usable without pretending exact note
    alignment.  The variation engine maps this sequence proportionally across
    MIDI note-on events.
    """
    sequence: list[str] = []
    for measure in reference.measures:
        if not measure.articulations:
            continue
        lowered = [a.lower() for a in measure.articulations]
        for preferred in ("accent", "marcato", "staccato", "tenuto", "legato", "slur"):
            if preferred in lowered:
                sequence.append(preferred)
                break
        else:
            sequence.append(lowered[0])
    return sequence


def score_reference_fermatas(reference: ScoreReference) -> list[str]:
    words: list[str] = []
    for measure in reference.measures:
        for word in measure.fermatas:
            cleaned = str(word).strip() or "fermata"
            if cleaned and cleaned not in words:
                words.append(cleaned)
    return words


def score_reference_tempo_words(reference: ScoreReference) -> list[str]:
    words: list[str] = []
    for measure in reference.measures:
        for word in measure.tempo_words:
            cleaned = str(word).strip()
            if cleaned and cleaned not in words:
                words.append(cleaned)
    return words


def score_reference_summary(reference: ScoreReference) -> tuple[str, str]:
    dynamics = sorted({d.lower() for measure in reference.measures for d in measure.dynamics})
    articulations = sorted({a for measure in reference.measures for a in measure.articulations})
    tempo_words = [w for measure in reference.measures for w in measure.tempo_words]
    fermatas = [f for measure in reference.measures for f in measure.fermatas]
    summary = f"OMR score data · {reference.measure_count} measures · {reference.note_count} score notes"
    parts: list[str] = []
    if dynamics:
        parts.append("dynamics: " + ", ".join(dynamics[:8]))
    if articulations:
        parts.append("articulations: " + ", ".join(articulations[:8]))
    if tempo_words:
        parts.append("tempo text: " + ", ".join(tempo_words[:5]))
    if fermatas:
        parts.append("fermatas: " + ", ".join(fermatas[:5]))
    return summary, "; ".join(parts) or "OMR produced structured score data."
