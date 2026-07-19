from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover - handled by the QtPdf fallback
    fitz = None  # type: ignore

from core.pdf_alignment_preview import detect_system_barlines


@dataclass(frozen=True)
class PdfMeasureBox:
    page_index: int
    system_index: int
    measure_index_in_system: int
    global_measure_index: int
    x0: int
    y0: int
    x1: int
    y1: int


@dataclass(frozen=True)
class PdfPageMeasureMap:
    page_index: int
    width: int
    height: int
    systems: int
    measures: list[PdfMeasureBox] = field(default_factory=list)


@dataclass(frozen=True)
class PdfMeasureMap:
    path: str
    page_count: int
    pages: list[PdfPageMeasureMap] = field(default_factory=list)
    renderer: str = "pymupdf"
    render_scale: float = 2.0

    @property
    def measures(self) -> list[PdfMeasureBox]:
        return [measure for page in self.pages for measure in page.measures]

    @property
    def measure_count(self) -> int:
        return sum(len(page.measures) for page in self.pages)

    def first_measure_for_page(self, page_number: int) -> int:
        page_index = max(0, int(page_number or 1) - 1)
        for page in self.pages:
            if page.page_index == page_index and page.measures:
                return page.measures[0].global_measure_index
        return 0

    def last_measure_for_page(self, page_number: int) -> int:
        page_index = max(0, int(page_number or 1) - 1)
        for page in self.pages:
            if page.page_index == page_index and page.measures:
                return page.measures[-1].global_measure_index
        return max(0, self.measure_count - 1)


@dataclass(frozen=True)
class RenderedMeasureCell:
    samples: bytes
    width: int
    height: int
    stride: int
    measure: PdfMeasureBox


def _group_consecutive(values: Iterable[int], *, max_gap: int) -> list[list[int]]:
    groups: list[list[int]] = []
    for value in values:
        value = int(value)
        if not groups or value - groups[-1][-1] > max_gap:
            groups.append([value])
        else:
            groups[-1].append(value)
    return groups


def detect_staff_systems(
    samples: bytes | bytearray | memoryview,
    width: int,
    height: int,
    stride: int,
) -> list[tuple[int, int]]:
    """Locate complete engraved systems from long horizontal staff-line ink."""
    width = max(0, int(width))
    height = max(0, int(height))
    stride = max(width, int(stride))
    view = memoryview(samples)
    if width <= 0 or height <= 0 or len(view) < stride * height:
        return []
    threshold_count = max(24, int(width * 0.10))
    candidate_rows: list[int] = []
    for y in range(height):
        start = y * stride
        row = view[start : start + width]
        dark = 0
        for value in row:
            if value < 165:
                dark += 1
                if dark >= threshold_count:
                    candidate_rows.append(y)
                    break
    line_groups = _group_consecutive(candidate_rows, max_gap=3)
    line_centers = [(group[0] + group[-1]) // 2 for group in line_groups]
    system_gap = max(42, int(height * 0.052))
    raw_systems = _group_consecutive(line_centers, max_gap=system_gap)
    systems: list[tuple[int, int]] = []
    for group in raw_systems:
        if len(group) < 8:
            continue
        padding = max(14, int(height * 0.016))
        y0 = max(0, group[0] - padding)
        y1 = min(height, group[-1] + padding)
        if y1 - y0 >= max(60, int(height * 0.08)):
            systems.append((y0, y1))
    return systems


def _render_page_gray_fitz(document, page_index: int, scale: float) -> tuple[bytes, int, int, int]:
    page = document.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csGRAY, alpha=False)
    if pix.width <= 0 or pix.height <= 0:
        raise RuntimeError(f"PDF page {page_index + 1} rendered empty")
    return bytes(pix.samples), int(pix.width), int(pix.height), int(pix.stride)


def _qimage_gray_bytes(image) -> tuple[bytes, int, int, int]:
    from PySide6.QtGui import QColor, QImage, QPainter

    # QPdfDocument renders the white paper as transparent on some platforms.
    # Converting that ARGB image directly to grayscale turns the whole page
    # black. Composite onto white first so the notation remains visible.
    if image.hasAlphaChannel():
        opaque = QImage(image.size(), QImage.Format_RGB32)
        opaque.fill(QColor("#ffffff"))
        painter = QPainter(opaque)
        painter.drawImage(0, 0, image)
        painter.end()
        image = opaque
    gray = image.convertToFormat(QImage.Format_Grayscale8)
    if gray.isNull() or gray.width() <= 0 or gray.height() <= 0:
        raise RuntimeError("QtPdf returned an empty page image")
    size = int(gray.sizeInBytes())
    bits = gray.bits()
    try:
        samples = bytes(bits[:size])
    except TypeError:  # compatibility with older PySide memoryview wrappers
        samples = bytes(bits)
    return samples, int(gray.width()), int(gray.height()), int(gray.bytesPerLine())


def _load_qt_pdf(source: Path):
    """Load a QPdfDocument and wait briefly when the backend is asynchronous."""
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtPdf import QPdfDocument

    document = QPdfDocument()
    load_error = document.load(str(source))
    if document.status() == QPdfDocument.Status.Loading:
        loop = QEventLoop()
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)

        def _status_changed(status) -> None:
            if status in (QPdfDocument.Status.Ready, QPdfDocument.Status.Error):
                loop.quit()

        document.statusChanged.connect(_status_changed)
        timeout.start(5000)
        loop.exec()
        try:
            document.statusChanged.disconnect(_status_changed)
        except Exception:
            pass
    if document.status() != QPdfDocument.Status.Ready or document.pageCount() <= 0:
        raise RuntimeError(f"QtPdf could not open the PDF (status {document.status()}, error {load_error})")
    return document


def _render_page_gray_qt(document, page_index: int, scale: float) -> tuple[bytes, int, int, int]:
    from PySide6.QtCore import QSize

    page_index = max(0, min(int(page_index), int(document.pageCount()) - 1))
    points = document.pagePointSize(page_index)
    width = max(600, int(round(float(points.width()) * max(1.0, float(scale)))))
    height = max(800, int(round(float(points.height()) * max(1.0, float(scale)))))
    image = document.render(page_index, QSize(width, height))
    return _qimage_gray_bytes(image)


def _crop_gray(
    samples: bytes,
    width: int,
    height: int,
    stride: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> tuple[bytes, int, int, int]:
    x0 = max(0, min(width, int(x0)))
    x1 = max(x0 + 1, min(width, int(x1)))
    y0 = max(0, min(height, int(y0)))
    y1 = max(y0 + 1, min(height, int(y1)))
    view = memoryview(samples)
    rows = [bytes(view[y * stride + x0 : y * stride + x1]) for y in range(y0, y1)]
    crop_width = x1 - x0
    return b"".join(rows), crop_width, y1 - y0, crop_width


def _build_map_from_renderer(source: Path, *, scale: float, renderer: str) -> PdfMeasureMap:
    pages: list[PdfPageMeasureMap] = []
    global_measure = 0
    if renderer == "pymupdf":
        if fitz is None:
            raise RuntimeError("PyMuPDF is unavailable")
        document_context = fitz.open(str(source))
        page_count = int(document_context.page_count)
        render = lambda page_index: _render_page_gray_fitz(document_context, page_index, scale)
        close = document_context.close
    else:
        document_context = _load_qt_pdf(source)
        page_count = int(document_context.pageCount())
        render = lambda page_index: _render_page_gray_qt(document_context, page_index, scale)
        close = document_context.close

    try:
        for page_index in range(page_count):
            samples, width, height, stride = render(page_index)
            systems = detect_staff_systems(samples, width, height, stride)
            page_measures: list[PdfMeasureBox] = []
            for system_index, (y0, y1) in enumerate(systems):
                crop_samples, crop_width, crop_height, crop_stride = _crop_gray(
                    samples, width, height, stride, 0, y0, width, y1
                )
                positions = detect_system_barlines(
                    crop_samples,
                    crop_width,
                    crop_height,
                    crop_stride,
                    dark_threshold=125,
                    min_height_fraction=0.35,
                )
                if len(positions) < 2:
                    continue
                for local_index in range(len(positions) - 1):
                    x0 = max(0, positions[local_index] - 2)
                    x1 = min(width, positions[local_index + 1] + 2)
                    if x1 - x0 < 12:
                        continue
                    page_measures.append(
                        PdfMeasureBox(
                            page_index=page_index,
                            system_index=system_index,
                            measure_index_in_system=local_index,
                            global_measure_index=global_measure,
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        )
                    )
                    global_measure += 1
            pages.append(
                PdfPageMeasureMap(
                    page_index=page_index,
                    width=width,
                    height=height,
                    systems=len(systems),
                    measures=page_measures,
                )
            )
    finally:
        try:
            close()
        except Exception:
            pass
    return PdfMeasureMap(
        path=str(source),
        page_count=page_count,
        pages=pages,
        renderer=renderer,
        render_scale=float(scale),
    )


def build_pdf_measure_map(pdf_path: str | Path, *, scale: float = 2.0) -> PdfMeasureMap:
    """Build a whole-document measure map with PyMuPDF or the bundled QtPdf renderer.

    PyMuPDF remains the preferred fast path.  QtPdf is part of PySide6 and is
    deliberately used as a full fallback so the alignment workspace never turns
    into a dependency-error pane on a normal Windows install.
    """
    source = Path(pdf_path)
    if not source.exists() or source.suffix.lower() != ".pdf":
        raise FileNotFoundError(str(source))
    errors: list[str] = []
    if fitz is not None:
        try:
            return _build_map_from_renderer(source, scale=scale, renderer="pymupdf")
        except Exception as exc:
            errors.append(f"PyMuPDF: {exc}")
    try:
        return _build_map_from_renderer(source, scale=scale, renderer="qtpdf")
    except Exception as exc:
        errors.append(f"QtPdf: {exc}")
    raise RuntimeError("; ".join(errors) or "No PDF renderer is available")


def _page_map_for(measure_map: PdfMeasureMap, page_index: int) -> PdfPageMeasureMap | None:
    for page in measure_map.pages:
        if page.page_index == page_index:
            return page
    return None


def _scaled_measure_box(
    measure: PdfMeasureBox,
    page_map: PdfPageMeasureMap | None,
    rendered_width: int,
    rendered_height: int,
) -> tuple[int, int, int, int]:
    if page_map is None or page_map.width <= 0 or page_map.height <= 0:
        return measure.x0, measure.y0, measure.x1, measure.y1
    sx = float(rendered_width) / float(page_map.width)
    sy = float(rendered_height) / float(page_map.height)
    return (
        int(round(measure.x0 * sx)),
        int(round(measure.y0 * sy)),
        int(round(measure.x1 * sx)),
        int(round(measure.y1 * sy)),
    )


def render_pdf_measure_cells(
    pdf_path: str | Path,
    measure_map: PdfMeasureMap,
    *,
    start_measure: int,
    bar_count: int,
    scale: float | None = None,
) -> tuple[list[RenderedMeasureCell], str]:
    """Return real PDF measure crops for one shared alignment window."""
    measures = measure_map.measures
    if not measures:
        return [], "No measure barlines were detected in this PDF"
    start = max(0, min(len(measures) - 1, int(start_measure or 0)))
    selected = measures[start : start + max(1, int(bar_count or 4))]
    if not selected:
        return [], "No mapped PDF measures in this section"

    source = Path(pdf_path)
    render_scale = float(scale if scale is not None else getattr(measure_map, "render_scale", 2.0) or 2.0)
    preferred = str(getattr(measure_map, "renderer", "pymupdf") or "pymupdf")
    renderer_order = [preferred] + [name for name in ("pymupdf", "qtpdf") if name != preferred]
    errors: list[str] = []

    for renderer in renderer_order:
        if renderer == "pymupdf" and fitz is None:
            continue
        page_cache: dict[int, tuple[bytes, int, int, int]] = {}
        cells: list[RenderedMeasureCell] = []
        document = None
        try:
            if renderer == "pymupdf":
                document = fitz.open(str(source))
                render = lambda page_index: _render_page_gray_fitz(document, page_index, render_scale)
            else:
                document = _load_qt_pdf(source)
                render = lambda page_index: _render_page_gray_qt(document, page_index, render_scale)

            for measure in selected:
                if measure.page_index not in page_cache:
                    page_cache[measure.page_index] = render(measure.page_index)
                samples, width, height, stride = page_cache[measure.page_index]
                page_map = _page_map_for(measure_map, measure.page_index)
                x0, y0, x1, y1 = _scaled_measure_box(measure, page_map, width, height)
                crop, crop_width, crop_height, crop_stride = _crop_gray(
                    samples, width, height, stride, x0, y0, x1, y1
                )
                cells.append(
                    RenderedMeasureCell(
                        samples=crop,
                        width=crop_width,
                        height=crop_height,
                        stride=crop_stride,
                        measure=measure,
                    )
                )
            break
        except Exception as exc:
            errors.append(f"{renderer}: {exc}")
            cells = []
        finally:
            if document is not None:
                try:
                    document.close()
                except Exception:
                    pass
    else:
        return [], "PDF section render failed: " + "; ".join(errors[-2:])

    if not cells:
        return [], "PDF section render failed: " + ("; ".join(errors[-2:]) or "no renderer")
    pages = sorted({cell.measure.page_index + 1 for cell in cells})
    page_label = f"page {pages[0]}" if len(pages) == 1 else f"pages {pages[0]}–{pages[-1]}"
    first = cells[0].measure.global_measure_index + 1
    last = cells[-1].measure.global_measure_index + 1
    renderer_label = "QtPdf fallback" if renderer == "qtpdf" else "PDF renderer"
    return cells, f"PDF {page_label} · score measures {first}–{last} · {len(cells)} bars mapped · {renderer_label}"
