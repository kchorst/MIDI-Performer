from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import io
import math
import os
import subprocess
import wave


@dataclass(frozen=True)
class ReferenceAudio:
    path: str
    format: str
    duration_seconds: float | None
    sample_rate: int | None
    channels: int | None
    peak_percent: int | None = None
    rms_percent: int | None = None
    loudness_curve: list[int] = field(default_factory=list)
    waveform_curve: list[int] = field(default_factory=list)
    attack_curve: list[int] = field(default_factory=list)
    motion_curve: list[int] = field(default_factory=list)
    analysis_summary: str | None = None
    analysis_detail: str | None = None

    def compact_text(self) -> str:
        duration = "duration unknown" if self.duration_seconds is None else _format_seconds(self.duration_seconds)
        rate = "rate unknown" if self.sample_rate is None else f"{self.sample_rate} Hz"
        channels = "channels unknown" if self.channels is None else f"{self.channels} ch"
        base = f"{self.format} · {duration} · {rate} · {channels}"
        if self.analysis_summary:
            return f"{base} · {self.analysis_summary}"
        return base


STRENGTH_TARGET_SPAN: dict[str, float] = {
    "Light": 3.0,
    "Medium": 5.0,
    "Strong": 7.0,
}

STRENGTH_BLEND: dict[str, float] = {
    "Light": 0.42,
    "Medium": 0.55,
    "Strong": 0.68,
}


@dataclass(frozen=True)
class ReferenceAudioGuidancePlan:
    """First-pass performance mimic guidance from a reference audio contour.

    This is deliberately conservative. It uses coarse loudness plus simple
    transient/energy-motion proxies. It still does not claim note-level
    audio-to-MIDI alignment or full rubato matching.
    """

    source_path: str
    source_format: str
    strength: str
    loudness_curve: tuple[int, ...]
    attack_curve: tuple[int, ...] = ()
    motion_curve: tuple[int, ...] = ()
    peak_percent: int | None = None
    rms_percent: int | None = None
    summary: str = "Audio guide"
    detail: str = "Reference audio loudness contour guidance."
    alignment_shift_percent: float = 0.0
    alignment_stretch_percent: float = 100.0

    def _aligned_fraction(self, midi_note_index: int, midi_note_count: int) -> float:
        if midi_note_count <= 1:
            raw = 0.0
        else:
            raw = max(0.0, min(1.0, float(max(0, midi_note_index)) / float(max(1, midi_note_count - 1))))
        shift = max(-0.80, min(0.80, float(self.alignment_shift_percent or 0) / 100.0))
        stretch = max(0.20, min(1.80, float(self.alignment_stretch_percent or 100) / 100.0))
        return max(0.0, min(1.0, (raw - shift) / stretch))

    @property
    def is_active(self) -> bool:
        return len(self.loudness_curve) >= 4 or len(self.attack_curve) >= 4 or len(self.motion_curve) >= 4

    @property
    def strength_target_span(self) -> float:
        return STRENGTH_TARGET_SPAN.get(self.strength, STRENGTH_TARGET_SPAN["Light"])

    @property
    def strength_blend(self) -> float:
        return STRENGTH_BLEND.get(self.strength, STRENGTH_BLEND["Light"])

    def _curve_value_for_midi_note(self, curve: tuple[int, ...], midi_note_index: int, midi_note_count: int) -> float:
        if not curve:
            return 0.0
        fraction = self._aligned_fraction(midi_note_index, midi_note_count)
        idx = int(round(fraction * max(0, len(curve) - 1)))
        idx = max(0, min(len(curve) - 1, idx))
        return float(curve[idx])

    @staticmethod
    def _normalized_curve_value(curve: tuple[int, ...], value: float) -> float:
        if not curve:
            return 0.0
        values = [float(v) for v in curve]
        mean = sum(values) / max(1, len(values))
        span = max(1.0, max(abs(v - mean) for v in values))
        return max(-1.0, min(1.0, (float(value) - mean) / span))

    def _normalized_for_note(self, curve: tuple[int, ...], midi_note_index: int, midi_note_count: int) -> float:
        return self._normalized_curve_value(curve, self._curve_value_for_midi_note(curve, midi_note_index, midi_note_count))

    def audio_led_velocity_for_midi_note(self, candidate_velocity: float, midi_note_index: int, midi_note_count: int) -> float:
        """Nudge velocity toward the reference performance's loudness contour.

        The reference contour is normalized around its own mean, so a loud
        recording does not simply make the MIDI loud.  It only contributes shape.
        """
        if not self.is_active:
            return candidate_velocity
        loud = self._normalized_for_note(self.loudness_curve, midi_note_index, midi_note_count)
        attack = self._normalized_for_note(self.attack_curve, midi_note_index, midi_note_count)
        motion = self._normalized_for_note(self.motion_curve, midi_note_index, midi_note_count)
        # Loudness remains the main dynamics contour; attack and motion add
        # audible emphasis without behaving like global volume.
        combined = max(-1.0, min(1.0, (loud * 0.70) + (attack * 0.22) + (motion * 0.08)))
        target = 78.0 + (combined * self.strength_target_span)
        return (float(candidate_velocity) * (1.0 - self.strength_blend)) + (target * self.strength_blend)

    def audio_led_timing_offset_for_midi_note(self, midi_note_index: int, midi_note_count: int, ticks_per_beat: int) -> int:
        """Small onset/pacing nudge from reference-audio energy motion.

        High attack/motion regions play a hair earlier; low-motion regions relax a
        hair later. This is intentionally tiny and phrase-level, not true onset
        matching yet.
        """
        if not self.is_active:
            return 0
        strength_scale = {"Light": 0.006, "Medium": 0.010, "Strong": 0.016}.get(self.strength, 0.006)
        attack = self._normalized_for_note(self.attack_curve, midi_note_index, midi_note_count)
        motion = self._normalized_for_note(self.motion_curve, midi_note_index, midi_note_count)
        combined = max(-1.0, min(1.0, (attack * 0.65) + (motion * 0.35)))
        return int(round(-combined * max(1, ticks_per_beat) * strength_scale))

    def audio_led_length_factor_for_midi_note(self, midi_note_index: int, midi_note_count: int) -> float:
        if not self.is_active:
            return 1.0
        attack = self._normalized_for_note(self.attack_curve, midi_note_index, midi_note_count)
        motion = self._normalized_for_note(self.motion_curve, midi_note_index, midi_note_count)
        strength_scale = {"Light": 0.015, "Medium": 0.026, "Strong": 0.040}.get(self.strength, 0.015)
        # More attack = slightly cleaner/shorter; calmer motion = slightly longer.
        delta = (-attack * 0.70 + (-motion) * 0.30) * strength_scale
        return max(0.92, min(1.08, 1.0 + delta))


def create_reference_audio_guidance(
    *,
    source_path: str,
    source_format: str = "Audio",
    loudness_curve: list[int] | tuple[int, ...] | None,
    attack_curve: list[int] | tuple[int, ...] | None = None,
    motion_curve: list[int] | tuple[int, ...] | None = None,
    strength: str = "Light",
    peak_percent: int | None = None,
    rms_percent: int | None = None,
    alignment_shift_percent: float = 0.0,
    alignment_stretch_percent: float = 100.0,
) -> ReferenceAudioGuidancePlan:
    strength = strength if strength in STRENGTH_TARGET_SPAN else "Light"
    curve = tuple(max(0, min(100, int(v))) for v in (loudness_curve or []) if v is not None)
    attack = tuple(max(0, min(100, int(v))) for v in (attack_curve or []) if v is not None)
    motion = tuple(max(0, min(100, int(v))) for v in (motion_curve or []) if v is not None)
    if len(curve) >= 4 or len(attack) >= 4 or len(motion) >= 4:
        spark = _sparkline(list(curve)) if curve else ""
        attack_spark = _sparkline(list(attack)) if attack else ""
        motion_spark = _sparkline(list(motion)) if motion else ""
        summary = f"Audio guide {strength} · dynamics/attack/pacing"
        detail = (
            "Uses reference-audio loudness, attack, and pacing proxies as conservative MIDI guidance. "
            f"Loudness {spark or 'n/a'}; attack {attack_spark or 'n/a'}; pacing {motion_spark or 'n/a'}. "
            "This is not full note-level audio alignment yet."
        )
    else:
        summary = f"Audio guide {strength} unavailable"
        detail = "Reference audio has no usable loudness/attack/pacing contour yet. WAV or FFmpeg analysis is needed before audio-guided takes can alter MIDI."
    return ReferenceAudioGuidancePlan(
        source_path=str(source_path),
        source_format=str(source_format or "Audio"),
        strength=strength,
        loudness_curve=curve,
        attack_curve=attack,
        motion_curve=motion,
        peak_percent=peak_percent,
        rms_percent=rms_percent,
        summary=summary,
        detail=detail,
        alignment_shift_percent=float(alignment_shift_percent or 0.0),
        alignment_stretch_percent=float(alignment_stretch_percent or 100.0),
    )



def _derive_audio_shape_curves(loudness_curve: list[int]) -> tuple[list[int], list[int]]:
    """Derive coarse attack and pacing proxies from segment loudness.

    This is intentionally lightweight: it is not true beat/onset detection, but
    it gives the audio guide more than plain volume by detecting where energy
    changes quickly and where the performance feels more/less active.
    """
    if len(loudness_curve) < 4:
        return [], []
    diffs = [0] + [abs(int(loudness_curve[i]) - int(loudness_curve[i - 1])) for i in range(1, len(loudness_curve))]
    max_diff = max(1, max(diffs))
    attack = [int(round((d / max_diff) * 100)) for d in diffs]
    # Motion combines current energy and local change, useful as a gentle pacing
    # proxy for timing/length nudges.
    max_loud = max(1, max(loudness_curve))
    motion = [int(round(((int(v) / max_loud) * 0.62 + (attack[i] / 100.0) * 0.38) * 100)) for i, v in enumerate(loudness_curve)]
    return attack, motion

def _format_seconds(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    minutes, sec = divmod(total, 60)
    return f"{minutes}:{sec:02d}" if minutes else f"{sec}s"


def _decode_sample(raw: bytes, sample_width: int) -> int:
    if sample_width == 1:
        return int(raw[0]) - 128
    if sample_width == 2:
        return int.from_bytes(raw, "little", signed=True)
    if sample_width == 3:
        # Sign-extend 24-bit little-endian PCM.
        value = int.from_bytes(raw + (b"\xff" if raw[2] & 0x80 else b"\x00"), "little", signed=True)
        return int(value)
    if sample_width == 4:
        return int.from_bytes(raw, "little", signed=True)
    return 0


def _max_abs_for_width(sample_width: int) -> float:
    if sample_width == 1:
        return 128.0
    return float((1 << (sample_width * 8 - 1)) - 1)


def _sparkline(values: list[int]) -> str:
    if not values:
        return ""
    chars = "▁▂▃▄▅▆▇█"
    return "".join(chars[min(len(chars) - 1, max(0, round((v / 100.0) * (len(chars) - 1))))] for v in values)


def _analyze_open_wav(wav: wave.Wave_read, *, target_segments: int = 64) -> tuple[float | None, int | None, int | None, int | None, int | None, list[int], list[int], str | None, str | None]:
    frames = wav.getnframes()
    rate = wav.getframerate()
    channels = wav.getnchannels()
    sample_width = wav.getsampwidth()
    duration = frames / float(rate) if rate else None
    if frames <= 0 or channels <= 0 or sample_width not in {1, 2, 3, 4}:
        return duration, rate, channels, None, None, [], [], None, "WAV metadata read; waveform analysis was not available for this encoding."

    segments = max(8, min(512, int(target_segments)))
    seg_sum_sq = [0.0] * segments
    seg_count = [0] * segments
    seg_min = [1.0] * segments
    seg_max = [-1.0] * segments
    total_sum_sq = 0.0
    total_count = 0
    peak = 0.0
    max_abs = _max_abs_for_width(sample_width)
    frame_index = 0
    chunk_frames = 4096
    frame_bytes = channels * sample_width

    while frame_index < frames:
        raw = wav.readframes(min(chunk_frames, frames - frame_index))
        if not raw:
            break
        for offset in range(0, len(raw) - frame_bytes + 1, frame_bytes):
            signed_values: list[float] = []
            frame_peak = 0.0
            for ch in range(channels):
                start = offset + ch * sample_width
                sample = _decode_sample(raw[start:start + sample_width], sample_width)
                normalized = max(-1.0, min(1.0, sample / max_abs))
                signed_values.append(normalized)
                frame_peak = max(frame_peak, abs(normalized))
            mono = sum(signed_values) / max(1, len(signed_values))
            peak = max(peak, frame_peak)
            total_sum_sq += frame_peak * frame_peak
            total_count += 1
            seg = min(segments - 1, int((frame_index / max(1, frames)) * segments))
            seg_sum_sq[seg] += frame_peak * frame_peak
            seg_count[seg] += 1
            seg_min[seg] = min(seg_min[seg], mono)
            seg_max[seg] = max(seg_max[seg], mono)
            frame_index += 1

    rms = math.sqrt(total_sum_sq / max(1, total_count)) if total_count else 0.0
    curve = [int(round(100 * math.sqrt(seg_sum_sq[i] / max(1, seg_count[i])))) for i in range(segments)]
    waveform: list[int] = []
    for i in range(segments):
        lo = 0.0 if seg_count[i] <= 0 else seg_min[i]
        hi = 0.0 if seg_count[i] <= 0 else seg_max[i]
        waveform.extend([int(round(100 * lo)), int(round(100 * hi))])
    peak_percent = int(round(100 * peak))
    rms_percent = int(round(100 * rms))
    summary = f"waveform/contour ready · peak {peak_percent}% · RMS {rms_percent}%"
    detail = f"{segments}-segment loudness contour: {_sparkline(curve)} · signed min/max waveform envelope ready · values {curve}"
    return duration, rate, channels, peak_percent, rms_percent, curve, waveform, summary, detail


def _analyze_wav_loudness(source: Path, *, target_segments: int = 512) -> tuple[float | None, int | None, int | None, int | None, int | None, list[int], list[int], str | None, str | None]:
    with wave.open(str(source), "rb") as wav:
        return _analyze_open_wav(wav, target_segments=target_segments)


def _popen_kwargs() -> dict:
    kwargs: dict = {"capture_output": True}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _analyze_pcm16_mono(raw: bytes, *, sample_rate: int = 22050, target_segments: int = 512) -> tuple[float | None, int | None, int | None, int | None, int | None, list[int], list[int], str | None, str | None]:
    """Analyze raw signed 16-bit mono PCM from FFmpeg.

    The display waveform is a real signed min/max envelope for each time bin.
    Loudness remains a separate RMS contour used by the conservative audio
    guidance engine.
    """
    if not raw:
        return None, sample_rate, 1, None, None, [], [], None, "FFmpeg returned no decoded PCM audio."
    sample_width = 2
    frame_count = len(raw) // sample_width
    if frame_count <= 0:
        return None, sample_rate, 1, None, None, [], [], None, "FFmpeg returned no complete PCM samples."
    segments = max(16, min(512, int(target_segments)))
    seg_sum_sq = [0.0] * segments
    seg_count = [0] * segments
    seg_min = [1.0] * segments
    seg_max = [-1.0] * segments
    total_sum_sq = 0.0
    total_count = 0
    peak = 0.0
    max_abs = 32767.0
    for idx in range(frame_count):
        offset = idx * sample_width
        sample = int.from_bytes(raw[offset:offset + sample_width], "little", signed=True)
        signed = max(-1.0, min(1.0, sample / max_abs))
        amp = abs(signed)
        peak = max(peak, amp)
        total_sum_sq += amp * amp
        total_count += 1
        seg = min(segments - 1, int((idx / max(1, frame_count)) * segments))
        seg_sum_sq[seg] += amp * amp
        seg_count[seg] += 1
        seg_min[seg] = min(seg_min[seg], signed)
        seg_max[seg] = max(seg_max[seg], signed)
    rms = math.sqrt(total_sum_sq / max(1, total_count)) if total_count else 0.0
    curve = [int(round(100 * math.sqrt(seg_sum_sq[i] / max(1, seg_count[i])))) for i in range(segments)]
    waveform: list[int] = []
    for i in range(segments):
        lo = 0.0 if seg_count[i] <= 0 else seg_min[i]
        hi = 0.0 if seg_count[i] <= 0 else seg_max[i]
        waveform.extend([int(round(100 * lo)), int(round(100 * hi))])
    peak_percent = int(round(100 * peak))
    rms_percent = int(round(100 * rms))
    duration = frame_count / float(sample_rate) if sample_rate else None
    summary = f"FFmpeg waveform/contour ready · peak {peak_percent}% · RMS {rms_percent}%"
    detail = f"Decoded raw PCM via FFmpeg. {segments}-segment loudness contour: {_sparkline(curve)} · signed min/max waveform envelope ready · values {curve}"
    return duration, sample_rate, 1, peak_percent, rms_percent, curve, waveform, summary, detail


def _analyze_with_ffmpeg(source: Path, ffmpeg_path: str | Path, *, target_segments: int = 512) -> tuple[float | None, int | None, int | None, int | None, int | None, list[int], list[int], str | None, str | None]:
    """Decode a non-WAV reference file through FFmpeg and reuse WAV analysis.

    This keeps the user workflow simple: load MP3/WAV/etc. If FFmpeg is present,
    MIDI Performer can build the same conservative loudness contour without
    asking the user to manually convert to WAV first.
    """
    cmd = [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        "22050",
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(cmd, timeout=45, check=False, **_popen_kwargs())
    except Exception as exc:  # noqa: BLE001
        return None, None, None, None, None, [], [], None, f"FFmpeg audio analysis failed: {exc}"
    if result.returncode != 0 or not result.stdout:
        err = (result.stderr or b"").decode("utf-8", errors="replace").strip() if isinstance(result.stderr, (bytes, bytearray)) else str(result.stderr or "").strip()
        return None, None, None, None, None, [], [], None, f"FFmpeg audio analysis failed: {err or 'no decoded audio'}"
    try:
        return _analyze_pcm16_mono(result.stdout, sample_rate=22050, target_segments=target_segments)
    except Exception as exc:  # noqa: BLE001
        return None, None, None, None, None, [], [], None, f"FFmpeg decoded audio, but PCM analysis failed: {exc}"


def read_reference_audio(path: str | Path, *, ffmpeg_path: str | Path | None = None) -> ReferenceAudio:
    """Read reference-audio metadata and first-pass loudness information.

    WAV loudness is analyzed with the standard library so the feature works
    without adding heavy DSP dependencies. Other formats are accepted as attached
    reference-performance files and can be analyzed later through FFmpeg/onset
    tooling.
    """
    source = Path(path)
    fmt = source.suffix.lower().lstrip(".") or "audio"
    if source.suffix.lower() == ".wav":
        duration, rate, channels, peak, rms, curve, waveform, summary, detail = _analyze_wav_loudness(source)
        return ReferenceAudio(
            str(source),
            "WAV",
            duration,
            rate,
            channels,
            peak_percent=peak,
            rms_percent=rms,
            loudness_curve=curve,
            waveform_curve=waveform,
            attack_curve=_derive_audio_shape_curves(curve)[0],
            motion_curve=_derive_audio_shape_curves(curve)[1],
            analysis_summary=(summary + " · attack/pacing ready") if summary and curve else summary,
            analysis_detail=detail,
        )
    if ffmpeg_path:
        duration, rate, channels, peak, rms, curve, waveform, summary, detail = _analyze_with_ffmpeg(source, ffmpeg_path)
        if summary:
            return ReferenceAudio(
                str(source),
                fmt.upper(),
                duration,
                rate,
                channels,
                peak_percent=peak,
                rms_percent=rms,
                loudness_curve=curve,
                waveform_curve=waveform,
                attack_curve=_derive_audio_shape_curves(curve)[0],
                motion_curve=_derive_audio_shape_curves(curve)[1],
                analysis_summary=(summary + " · attack/pacing ready") if summary and curve else summary,
                analysis_detail=detail,
            )
        fallback_detail = detail or "FFmpeg did not return a usable loudness contour."
    else:
        fallback_detail = "Install/configure FFmpeg or load WAV for built-in loudness-contour analysis."
    return ReferenceAudio(
        str(source),
        fmt.upper(),
        None,
        None,
        None,
        analysis_summary="attached · audio guide unavailable",
        analysis_detail=f"Reference performance is attached, but no usable loudness contour is available yet. {fallback_detail}",
    )
