from __future__ import annotations

from pathlib import Path
import os
import threading
from typing import Any, Optional

from core.fluidsynth_player import FluidSynthError


class InProcessFluidSynthBackend:
    """Low-latency in-process FluidSynth backend.

    The previous live backend controlled a persistent FluidSynth *process* through
    stdin. That was much better than one-shot playback, but Windows could still
    delay audio startup or command delivery. This backend uses pyFluidSynth's
    direct Synth object when available, so MIDI Performer's scheduler talks to the
    synth in-process instead of through a shell pipe.

    It remains optional because pyFluidSynth requires the FluidSynth native DLL to
    be installed or bundled. A hybrid wrapper falls back to the shell backend when
    this backend is unavailable.
    """

    backend_name = "In-process FluidSynth"

    def __init__(self, app_root: str | Path | None = None) -> None:
        self.app_root = Path(app_root) if app_root else None
        self.synth: Any | None = None
        self.soundfont_path: str | None = None
        self.sfid: int | None = None
        self.gain: float = 0.5
        self._lock = threading.RLock()
        self._bank_msb: dict[int, int] = {ch: 0 for ch in range(16)}
        self._bank_lsb: dict[int, int] = {ch: 0 for ch in range(16)}
        self._dll_dirs: list[Any] = []
        self._fluidsynth_module: Any | None = None
        self._keepalive_active = False
        self._keepalive_channel = 15
        self._keepalive_note = 0
        self.active_driver: str | None = None

    def audio_ready_delay_seconds(self) -> float:
        # Keep readiness preparation short and clean. A long held keepalive
        # improved wake-up on some machines but caused crackling.
        # This delay is paid during load only; Play should never wait seconds.
        if os.name == "nt":
            try:
                return float(os.environ.get("MIDI_PERFORMER_AUDIO_READY_SECONDS", "0.75"))
            except Exception:
                return 0.75
        return 0.0

    def _add_windows_dll_path(self) -> None:
        if os.name != "nt" or not self.app_root:
            return
        candidates = [
            self.app_root / "vendor" / "fluidsynth" / "bin",
            self.app_root / "vendor" / "fluidsynth",
        ]
        for candidate in candidates:
            if candidate.exists():
                os.environ["PATH"] = f"{candidate}{os.pathsep}" + os.environ.get("PATH", "")
                add_dll_directory = getattr(os, "add_dll_directory", None)
                if callable(add_dll_directory):
                    try:
                        self._dll_dirs.append(add_dll_directory(str(candidate)))
                    except Exception:
                        pass

    def _import_fluidsynth(self) -> Any:
        if self._fluidsynth_module is not None:
            return self._fluidsynth_module
        self._add_windows_dll_path()
        try:
            import fluidsynth  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on local Windows setup
            raise FluidSynthError(
                "Low-latency in-process FluidSynth is not available. Install pyFluidSynth "
                "and make the FluidSynth native library/DLL available."
            ) from exc
        self._fluidsynth_module = fluidsynth
        return fluidsynth

    @staticmethod
    def _clamp_gain(value: float | int | None) -> float:
        try:
            gain = float(value) if value is not None else 0.5
        except (TypeError, ValueError):
            gain = 0.5
        return max(0.0, min(1.5, gain))

    def is_running(self) -> bool:
        return self.synth is not None and self.sfid is not None

    def _driver_candidates(self) -> list[str | None]:
        requested = os.environ.get("MIDI_PERFORMER_AUDIO_DRIVER", "").strip().lower()
        if requested:
            if requested in {"default", "auto", "none"}:
                return [None]
            return [requested]
        if os.name == "nt":
            # Match the known-good Windows output route: DirectSound first.
            return ["dsound", "wasapi", None]
        return [None]

    def start(self, soundfont_path: str | Path, gain: float = 0.5) -> None:
        sf = Path(soundfont_path)
        if not sf.exists():
            raise FluidSynthError(f"SoundFont file not found: {sf}")
        sf_str = str(sf)
        gain = self._clamp_gain(gain)
        with self._lock:
            if self.is_running() and self.soundfont_path == sf_str:
                self.set_gain(gain)
                return
            self.close()
            fluidsynth = self._import_fluidsynth()
            last_error: Exception | None = None
            for driver in self._driver_candidates():
                synth = None
                try:
                    # Small buffers are intentional: transport timing is owned by
                    # PlaybackEngine, so the synth should not add several seconds of
                    # audio buffering before the first audible note.
                    try:
                        synth = fluidsynth.Synth(gain=gain, samplerate=44100)
                    except TypeError:
                        synth = fluidsynth.Synth(gain=gain)
                    # Favor stable buffers over ultra-small buffers. An aggressive
                    # low-latency keepalive approach caused audible crackle on Windows.
                    for key, value in {
                        "audio.period-size": 512 if os.name == "nt" else 256,
                        "audio.periods": 4,
                    }.items():
                        try:
                            synth.setting(key, value)
                        except Exception:
                            pass
                    if driver:
                        synth.start(driver=driver)
                    else:
                        synth.start()
                    sfid = int(synth.sfload(sf_str))
                    if sfid < 0:
                        raise FluidSynthError(f"Could not load SoundFont: {sf}")
                    self.synth = synth
                    self.sfid = sfid
                    self.soundfont_path = sf_str
                    self.gain = gain
                    self.active_driver = str(driver or "default")
                    self._bank_msb = {ch: 0 for ch in range(16)}
                    self._bank_lsb = {ch: 0 for ch in range(16)}
                    # Put every melodic channel on a valid GM preset immediately.
                    # Drum channel uses bank 128 in FluidSynth-compatible GM soundfonts.
                    for ch in range(16):
                        preset_bank = 128 if ch == 9 else 0
                        try:
                            self.synth.program_select(ch, sfid, preset_bank, 0)
                        except Exception:
                            pass
                    return
                except Exception as exc:
                    last_error = exc
                    try:
                        if synth is not None:
                            synth.delete()
                    except Exception:
                        pass
                    self.synth = None
                    self.sfid = None
                    self.soundfont_path = None
                    self.active_driver = None
                    continue
            raise FluidSynthError(f"In-process FluidSynth failed to start. {last_error or ''}")

    def prime_audio(self) -> None:
        # Avoid inaudible held or rapid notes. A note-based keepalive produced
        # crackling and artifacts on the Windows test machine.
        # Controller context is safe and leaves no sounding state behind.
        if not self.synth:
            return
        try:
            for ch in range(16):
                self.control_change(ch, 7, 100)
                self.control_change(ch, 11, 127)
        except Exception:
            pass

    def start_keepalive(self) -> None:
        # Disabled by default because held inaudible notes can still wake or disturb
        # some Windows audio paths and produce crackle. Keep the method as a no-op
        # so older engine code remains compatible.
        self._keepalive_active = False

    def stop_keepalive(self) -> None:
        self._keepalive_active = False

    def set_gain(self, gain: float) -> None:
        gain = self._clamp_gain(gain)
        self.gain = gain
        if not self.synth:
            return
        try:
            # pyFluidSynth exposes the generic setting method.
            self.synth.setting("synth.gain", gain)
        except Exception:
            # Some versions do not permit live gain setting. Playback continues;
            # future takes/export still preserve volume separately.
            pass

    def _bank_for_channel(self, channel: int) -> int:
        channel = max(0, min(15, int(channel)))
        if channel == 9:
            return 128
        msb = max(0, min(127, int(self._bank_msb.get(channel, 0))))
        lsb = max(0, min(127, int(self._bank_lsb.get(channel, 0))))
        # Most GM files use bank 0. Combining MSB/LSB preserves banked patches when present.
        return (msb * 128) + lsb if (msb or lsb) else 0

    def program_change(self, channel: int, program: int) -> None:
        with self._lock:
            if not self.synth or self.sfid is None:
                raise FluidSynthError("In-process FluidSynth backend is not running.")
            ch = max(0, min(15, int(channel)))
            prog = max(0, min(127, int(program)))
            try:
                self.synth.program_select(ch, self.sfid, self._bank_for_channel(ch), prog)
            except Exception:
                # Fallback for pyFluidSynth builds exposing program_change only.
                self.synth.program_change(ch, prog)

    def control_change(self, channel: int, control: int, value: int) -> None:
        with self._lock:
            if not self.synth:
                raise FluidSynthError("In-process FluidSynth backend is not running.")
            ch = max(0, min(15, int(channel)))
            cc = max(0, min(127, int(control)))
            val = max(0, min(127, int(value)))
            if cc == 0:
                self._bank_msb[ch] = val
            elif cc == 32:
                self._bank_lsb[ch] = val
            self.synth.cc(ch, cc, val)

    def note_on(self, channel: int, note: int, velocity: int) -> None:
        with self._lock:
            if not self.synth:
                raise FluidSynthError("In-process FluidSynth backend is not running.")
            self.synth.noteon(
                max(0, min(15, int(channel))),
                max(0, min(127, int(note))),
                max(0, min(127, int(velocity))),
            )

    def note_off(self, channel: int, note: int) -> None:
        with self._lock:
            if not self.synth:
                raise FluidSynthError("In-process FluidSynth backend is not running.")
            self.synth.noteoff(max(0, min(15, int(channel))), max(0, min(127, int(note))))

    def pitch_bend(self, channel: int, value: int) -> None:
        with self._lock:
            if not self.synth:
                raise FluidSynthError("In-process FluidSynth backend is not running.")
            raw = max(0, min(16383, int(value) + 8192))
            try:
                self.synth.pitch_bend(max(0, min(15, int(channel))), raw)
            except Exception:
                pass

    def all_notes_off(self) -> None:
        if not self.synth:
            return
        for ch in range(16):
            try:
                self.synth.cc(ch, 123, 0)
                self.synth.cc(ch, 120, 0)
                self.synth.cc(ch, 64, 0)
            except Exception:
                break

    def reset_controllers(self) -> None:
        if not self.synth:
            return
        for ch in range(16):
            try:
                self.synth.cc(ch, 121, 0)
            except Exception:
                break

    def close(self) -> None:
        with self._lock:
            try:
                self.stop_keepalive()
            except Exception:
                pass
            synth = self.synth
            self.synth = None
            self.sfid = None
            self.soundfont_path = None
            self.active_driver = None
            if synth is not None:
                try:
                    synth.delete()
                except Exception:
                    pass
