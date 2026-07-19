from __future__ import annotations

from collections.abc import Sequence


def detect_system_barlines(
    samples: bytes | bytearray | memoryview,
    width: int,
    height: int,
    stride: int,
    *,
    dark_threshold: int = 105,
    min_height_fraction: float = 0.55,
) -> list[int]:
    """Detect full-system vertical barlines in an 8-bit grayscale score crop.

    Engraved barlines cross most staves in a system while stems and noteheads do
    not. The function intentionally uses only grayscale bytes so it is fast,
    deterministic, and testable without a GUI or an OMR dependency.
    """
    width = max(0, int(width))
    height = max(0, int(height))
    stride = max(width, int(stride))
    if width <= 0 or height <= 0:
        return []
    view = memoryview(samples)
    if len(view) < stride * height:
        return []

    counts = [0] * width
    threshold = max(0, min(255, int(dark_threshold)))
    for y in range(height):
        row_start = y * stride
        for x in range(width):
            if view[row_start + x] < threshold:
                counts[x] += 1

    min_vertical_ink = max(24, int(height * max(0.10, min(0.95, float(min_height_fraction)))))
    candidates = [x for x, count in enumerate(counts) if count >= min_vertical_ink]
    groups: list[list[int]] = []
    for x in candidates:
        if not groups or x - groups[-1][-1] > 4:
            groups.append([x])
        else:
            groups[-1].append(x)

    positions: list[int] = []
    for group in groups:
        best = max(group, key=lambda x: counts[x])
        if positions and best - positions[-1] <= 12:
            if counts[best] > counts[positions[-1]]:
                positions[-1] = best
        else:
            positions.append(best)

    left_limit = int(width * 0.04)
    right_limit = int(width * 0.98)
    return [x for x in positions if left_limit <= x <= right_limit]


def mapped_measure_slices(barlines: Sequence[int], requested_bars: int, image_width: int) -> list[tuple[int, int]]:
    """Return safe image x-ranges for the requested number of detected measures."""
    points = sorted({max(0, min(int(image_width), int(x))) for x in barlines})
    if len(points) < 2 or image_width <= 0:
        return []
    count = min(max(0, int(requested_bars)), len(points) - 1)
    result: list[tuple[int, int]] = []
    for idx in range(count):
        x0 = max(0, points[idx] - 1)
        x1 = min(int(image_width), points[idx + 1] + 1)
        if x1 > x0:
            result.append((x0, x1))
    return result
