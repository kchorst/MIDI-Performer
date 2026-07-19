from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import wave
from typing import Optional


class FluidSynthError(RuntimeError):
    pass


class FluidSynthPlayer:
    """Small wrapper around FluidSynth command-line playback/rendering."""

    def __init__(self, app_root: str | Path | None = None, executable_path: str | Path | None = None) -> None:
        self.process: Optional[subprocess.Popen] = None
        self.app_root = Path(app_root) if app_root else Path(__file__).resolve().parents[1]
        self.executable_path: Path | None = Path(executable_path).expanduser() if executable_path else None

    def set_executable_path(self, executable_path: str | Path | None) -> None:
        self.executable_path = Path(executable_path).expanduser() if executable_path else None


    def find_executable(self) -> Optional[Path | str]:
        if self.executable_path and self.executable_path.is_file():
            return self.executable_path
        vendor_exe = self.app_root / "vendor" / "fluidsynth" / "bin" / "fluidsynth.exe"
        if vendor_exe.exists():
            return vendor_exe
        vendor_root = self.app_root / "vendor" / "fluidsynth"
        if vendor_root.exists():
            matches = sorted(vendor_root.glob("**/fluidsynth.exe"))
            if matches:
                return matches[0]
        return shutil.which("fluidsynth") or shutil.which("fluidsynth.exe")

    def find_ffmpeg(self) -> Optional[Path | str]:
        vendor_exe = self.app_root / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe"
        if vendor_exe.exists():
            return vendor_exe
        vendor_root = self.app_root / "vendor" / "ffmpeg"
        if vendor_root.exists():
            matches = sorted(vendor_root.glob("**/ffmpeg.exe"))
            if matches:
                return matches[0]
        return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

    def is_available(self) -> bool:
        return self.find_executable() is not None

    def _popen_kwargs(self) -> dict:
        kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = startupinfo
        return kwargs

    def _run_hidden(self, cmd: list[str]) -> subprocess.CompletedProcess:
        kwargs = self._popen_kwargs()
        return subprocess.run(cmd, check=False, **kwargs)

    @staticmethod
    def _gain_arg(volume_gain: float | int | None) -> str:
        """FluidSynth gain. Default FluidSynth gain is conservative; keep app range safe."""
        try:
            value = float(volume_gain) if volume_gain is not None else 0.5
        except (TypeError, ValueError):
            value = 0.5
        value = max(0.0, min(1.5, value))
        return f"{value:.3f}"

    def play(self, midi_path: str | Path, soundfont_path: str | Path, volume_gain: float = 0.5) -> None:
        self.stop()
        midi = Path(midi_path)
        sf = Path(soundfont_path)
        if not midi.exists():
            raise FluidSynthError(f"MIDI file not found: {midi}")
        if not sf.exists():
            raise FluidSynthError(f"SoundFont file not found: {sf}")

        exe = self.find_executable()
        if not exe:
            raise FluidSynthError(
                "FluidSynth was not found. Expected it at "
                "the FluidSynth location selected in Settings, vendor\\fluidsynth\\bin\\fluidsynth.exe, or Windows PATH."
            )

        cmd = [str(exe), "-i", "-n", "-a", "dsound", "-g", self._gain_arg(volume_gain), str(sf), str(midi)]
        self.process = subprocess.Popen(cmd, **self._popen_kwargs())


    @staticmethod
    def _clean_wav_start(wav_path: str | Path, fade_ms: float = 28.0, silence_ms: float = 3.0) -> None:
        """Apply a tiny start guard to remove FluidSynth render clicks/pops.

        Some Windows/FluidSynth render paths can write a single sharp transient at
        frame zero.  This is not musical MIDI content; it is an export artifact.
        Zeroing only the first few milliseconds and fading the next few
        milliseconds avoids the loud click without changing the take structure.
        """
        path = Path(wav_path)
        if not path.exists():
            return
        try:
            with wave.open(str(path), "rb") as reader:
                params = reader.getparams()
                frames = reader.readframes(params.nframes)
        except (wave.Error, OSError):
            return
        if params.sampwidth != 2 or params.nframes <= 0 or params.framerate <= 0:
            return
        channels = max(1, int(params.nchannels or 1))
        silence_frames = max(0, int(params.framerate * float(silence_ms) / 1000.0))
        fade_frames = max(1, int(params.framerate * float(fade_ms) / 1000.0))
        total_guard_frames = min(params.nframes, silence_frames + fade_frames)
        if total_guard_frames <= 0:
            return
        sample_count = len(frames) // 2
        samples = bytearray(frames)
        guard_samples = total_guard_frames * channels
        silence_samples = min(sample_count, silence_frames * channels)
        for idx in range(silence_samples):
            byte_index = idx * 2
            samples[byte_index:byte_index + 2] = b"\x00\x00"
        for idx in range(silence_samples, min(sample_count, guard_samples)):
            frame_index = idx // channels
            fade_index = max(0, frame_index - silence_frames)
            scale = min(1.0, max(0.0, fade_index / float(fade_frames)))
            byte_index = idx * 2
            value = int.from_bytes(samples[byte_index:byte_index + 2], byteorder="little", signed=True)
            value = int(value * scale)
            samples[byte_index:byte_index + 2] = int(value).to_bytes(2, byteorder="little", signed=True)
        try:
            with wave.open(str(path), "wb") as writer:
                writer.setparams(params)
                writer.writeframes(bytes(samples))
        except (wave.Error, OSError):
            return

    def render_wav(
        self,
        midi_path: str | Path,
        soundfont_path: str | Path,
        output_wav: str | Path,
        volume_gain: float = 0.5,
    ) -> Path:
        midi = Path(midi_path)
        sf = Path(soundfont_path)
        out = Path(output_wav)
        out.parent.mkdir(parents=True, exist_ok=True)
        exe = self.find_executable()
        if not exe:
            raise FluidSynthError("FluidSynth was not found, so WAV export cannot run.")
        cmd = [
            str(exe),
            "-n",
            "-i",
            "-g",
            self._gain_arg(volume_gain),
            "-T",
            "wav",
            "-O",
            "s16",
            "-F",
            str(out),
            "-r",
            "44100",
            str(sf),
            str(midi),
        ]
        result = self._run_hidden(cmd)
        if result.returncode != 0 or not out.exists():
            raise FluidSynthError("WAV export failed. Check that the MIDI and SoundFont can play first.")
        self._clean_wav_start(out)
        return out

    def render_mp3(
        self,
        midi_path: str | Path,
        soundfont_path: str | Path,
        output_mp3: str | Path,
        volume_gain: float = 0.5,
    ) -> Path:
        mp3 = Path(output_mp3)
        temp_wav = mp3.with_suffix(".tmp_render.wav")
        self.render_wav(midi_path, soundfont_path, temp_wav, volume_gain=volume_gain)
        ffmpeg = self.find_ffmpeg()
        if not ffmpeg:
            try:
                temp_wav.unlink(missing_ok=True)
            except TypeError:
                if temp_wav.exists():
                    temp_wav.unlink()
            raise FluidSynthError(
                "MP3 export needs FFmpeg. Put ffmpeg.exe at vendor\\ffmpeg\\bin\\ffmpeg.exe "
                "or install FFmpeg on PATH. WAV and MIDI export still work."
            )
        # Mid-level, broadly compatible MP3 default for MIDI Performer exports.
        cmd = [str(ffmpeg), "-y", "-i", str(temp_wav), "-codec:a", "libmp3lame", "-b:a", "192k", str(mp3)]
        result = self._run_hidden(cmd)
        try:
            temp_wav.unlink(missing_ok=True)
        except TypeError:
            if temp_wav.exists():
                temp_wav.unlink()
        if result.returncode != 0 or not mp3.exists():
            raise FluidSynthError("MP3 export failed. Try WAV export or check FFmpeg.")
        return mp3

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
