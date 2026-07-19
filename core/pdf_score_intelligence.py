from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable


DYNAMIC_ORDER = ["pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff", "sfz", "fp"]
TEMPO_WORDS = [
    "grave", "largo", "lento", "adagio", "andante", "moderato", "allegretto",
    "allegro", "vivace", "presto", "prestissimo", "rit", "rit.", "rall",
    "rall.", "accelerando", "accel", "accel.", "a tempo", "rubato",
]
ARTICULATION_WORDS = [
    "staccato", "legato", "tenuto", "marcato", "accent", "accented", "slur", "phrase",
    "crescendo", "cresc", "cresc.", "diminuendo", "dim", "dim.", "decrescendo",
]
FERMATA_WORDS = ["fermata", "hold"]


@dataclass(frozen=True)
class PdfScoreAnalysis:
    """Lightweight PDF score analysis for the PDF-first workflow.

    This is intentionally not full OMR. It extracts page count and any embedded text
    markings that a PDF exposes. Many scanned/engraved scores expose little or no
    text, so the analysis clearly reports when true OMR is still needed.
    """

    path: str
    page_count: int | None
    file_size_kb: int
    extracted_text: str = ""
    dynamics: list[str] = field(default_factory=list)
    dynamic_sequence: list[str] = field(default_factory=list)
    tempo_words: list[str] = field(default_factory=list)
    articulation_words: list[str] = field(default_factory=list)
    fermata_words: list[str] = field(default_factory=list)
    confidence: str = "PDF attached"
    summary: str = ""
    detail: str = ""
    page_range: tuple[int, int] | None = None
    analyzed_pages_label: str = "all pages"

    @property
    def has_guidance(self) -> bool:
        return bool(self.dynamic_sequence or self.tempo_words or self.articulation_words)

    @property
    def has_dynamic_guidance(self) -> bool:
        return bool(self.dynamic_sequence)

    def compact_text(self) -> str:
        if self.summary:
            return self.summary
        return f"PDF score · {self.analyzed_pages_label}"


def analyze_pdf_score(path: str | Path, page_range: tuple[int, int] | None = None) -> PdfScoreAnalysis:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(str(source))
    data = source.read_bytes()
    page_count = _page_count(source, data)
    normalized_range = normalize_page_range(page_range, page_count)
    analyzed_pages_label = page_range_label(normalized_range, page_count)
    file_size_kb = max(1, int(round(len(data) / 1024)))
    text = _extract_pdf_text(source, data, normalized_range)
    cleaned = _clean_text(text)
    dynamic_sequence = _find_dynamic_sequence(cleaned)
    dynamics = sorted(set(dynamic_sequence), key=_dynamic_sort_key)
    tempos = _find_known_words(cleaned, TEMPO_WORDS)
    articulations = _find_known_words(cleaned, ARTICULATION_WORDS)
    fermatas = _find_known_words(cleaned, FERMATA_WORDS)

    page_text = analyzed_pages_label
    found_parts: list[str] = []
    if dynamics:
        found_parts.append("dynamics " + ", ".join(dynamics[:8]))
    if tempos:
        found_parts.append("tempo text " + ", ".join(tempos[:5]))
    if articulations:
        found_parts.append("score words " + ", ".join(articulations[:6]))
    if fermatas:
        found_parts.append("fermatas " + ", ".join(fermatas[:3]))

    if dynamic_sequence:
        confidence = "Score guide ready from embedded PDF markings"
        summary = f"PDF analyzed · {page_text} · {len(dynamic_sequence)} dynamic mark{'s' if len(dynamic_sequence) != 1 else ''} found"
        detail = "Embedded PDF text exposed musical markings: " + "; ".join(found_parts)
    elif tempos or articulations or fermatas:
        confidence = "Partial score guide from embedded PDF text"
        summary = f"PDF analyzed · {page_text} · musical text found"
        detail = "Embedded PDF text exposed musical words, but no usable dynamic sequence: " + "; ".join(found_parts)
    else:
        confidence = "PDF attached — true OMR still needed"
        summary = f"PDF attached · {page_text} · no embedded score markings found"
        detail = (
            "The PDF is saved with the project, but this build only reads embedded text. "
            "Scanned or image-only sheet music needs a real OMR engine before notes, dynamics, and articulations can be aligned."
        )

    return PdfScoreAnalysis(
        path=str(source),
        page_count=page_count,
        file_size_kb=file_size_kb,
        extracted_text=cleaned[:4000],
        dynamics=dynamics,
        dynamic_sequence=dynamic_sequence,
        tempo_words=tempos,
        articulation_words=articulations,
        fermata_words=fermatas,
        confidence=confidence,
        summary=summary,
        detail=detail,
        page_range=normalized_range,
        analyzed_pages_label=analyzed_pages_label,
    )


def normalize_page_range(page_range: tuple[int, int] | None, page_count: int | None = None) -> tuple[int, int] | None:
    if not page_range:
        return None
    try:
        start = int(page_range[0])
        end = int(page_range[1])
    except Exception:
        return None
    if start <= 0 or end <= 0:
        return None
    if start > end:
        start, end = end, start
    if page_count and page_count > 0:
        start = max(1, min(start, page_count))
        end = max(1, min(end, page_count))
        if start == 1 and end == page_count:
            return None
    return (start, end)


def page_range_label(page_range: tuple[int, int] | None, page_count: int | None = None) -> str:
    total = f" of {page_count}" if page_count else ""
    if not page_range:
        if page_count:
            return f"{page_count} page{'s' if page_count != 1 else ''}"
        return "page count unknown"
    start, end = page_range
    if start == end:
        return f"page {start}{total}"
    return f"pages {start}–{end}{total}"


def export_pdf_page_range(source: str | Path, target: str | Path, page_range: tuple[int, int] | None = None) -> Path:
    """Create a focused PDF subset for OMR, returning the original when no subset is needed."""
    source_path = Path(source)
    target_path = Path(target)
    data = source_path.read_bytes()
    page_count = _page_count(source_path, data)
    normalized = normalize_page_range(page_range, page_count)
    if not normalized:
        return source_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fitz  # type: ignore

        with fitz.open(str(source_path)) as src:
            selected = _selected_page_indices(len(src), normalized)
            if not selected or len(selected) == len(src):
                return source_path
            out = fitz.open()
            for idx in selected:
                out.insert_pdf(src, from_page=idx, to_page=idx)
            out.save(str(target_path))
            out.close()
        return target_path
    except Exception:
        pass
    try:
        from pypdf import PdfReader, PdfWriter  # type: ignore

        reader = PdfReader(str(source_path))
        selected = _selected_page_indices(len(reader.pages), normalized)
        if not selected or len(selected) == len(reader.pages):
            return source_path
        writer = PdfWriter()
        for idx in selected:
            writer.add_page(reader.pages[idx])
        with target_path.open("wb") as fh:
            writer.write(fh)
        return target_path
    except Exception as exc:
        raise RuntimeError(f"Could not create focused PDF page range {page_range_label(normalized, page_count)}: {exc}") from exc


def _page_count(path: Path, data: bytes) -> int | None:
    try:
        import fitz  # type: ignore

        with fitz.open(str(path)) as doc:
            count = int(doc.page_count)
            if count > 0:
                return count
    except Exception:
        pass
    try:
        matches = re.findall(rb"/Type\s*/Page\b", data)
        return len(matches) or None
    except Exception:
        return None


def _selected_page_indices(page_count: int, page_range: tuple[int, int] | None = None) -> list[int]:
    if page_count <= 0:
        return []
    normalized = normalize_page_range(page_range, page_count)
    if not normalized:
        return list(range(page_count))
    start, end = normalized
    return list(range(max(0, start - 1), min(page_count, end)))


def _extract_pdf_text(path: Path, data: bytes, page_range: tuple[int, int] | None = None) -> str:
    # Prefer PyMuPDF when available; it is fast and handles many PDFs. The app
    # still works without it because fallback extraction gives a useful status.
    try:
        import fitz  # type: ignore

        chunks: list[str] = []
        with fitz.open(str(path)) as doc:
            for idx in _selected_page_indices(len(doc), page_range):
                chunks.append(doc[idx].get_text("text") or "")
        text = "\n".join(chunks).strip()
        if text:
            return text
    except Exception:
        pass

    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        chunks = [(reader.pages[idx].extract_text() or "") for idx in _selected_page_indices(len(reader.pages), page_range)]
        text = "\n".join(chunks).strip()
        if text:
            return text
    except Exception:
        pass

    # Crude fallback for simple PDFs and tests. It is not OMR; it only looks for
    # literal text tokens that may already be embedded in the PDF bytes. Because
    # there is no page structure in this fallback, page ranges cannot be honored.
    decoded = data.decode("latin1", errors="ignore")
    literal_strings = re.findall(r"\(([^()]{0,200})\)", decoded)
    if literal_strings:
        return "\n".join(literal_strings)
    return decoded[:20000]


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _find_dynamic_sequence(text: str) -> list[str]:
    if not text:
        return []
    # Match common standalone dynamic marks, but avoid letters inside normal words.
    alternatives = sorted(DYNAMIC_ORDER, key=len, reverse=True)
    pattern = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(d) for d in alternatives) + r")(?![A-Za-z])")
    return [match.group(1).lower() for match in pattern.finditer(text)]


def _find_known_words(text: str, words: Iterable[str]) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    lowered = text.lower()
    for word in words:
        plain = word.lower()
        if plain.endswith("."):
            pattern = r"(?<![a-z])" + re.escape(plain.rstrip(".")) + r"\.?" + r"(?![a-z])"
        else:
            pattern = r"(?<![a-z])" + re.escape(plain) + r"(?![a-z])"
        if re.search(pattern, lowered):
            label = word.rstrip(".") if word in {"rit.", "rall.", "accel.", "cresc.", "dim."} else word
            if label not in found:
                found.append(label)
    return found


def _dynamic_sort_key(dynamic: str) -> int:
    try:
        return DYNAMIC_ORDER.index(dynamic)
    except ValueError:
        return len(DYNAMIC_ORDER)
