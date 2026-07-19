# Downloads, Privacy, and Third-Party Components

## Local processing

MIDI Performer 0.7.0 processes MIDI files, project files, PDF scores, and reference audio locally. It does not include user accounts, cloud storage, telemetry, or analytics.

## User-initiated network access

Network access occurs only when the user chooses a download action or runs a sample helper.

### Install FluidSynth

The application queries the official FluidSynth GitHub releases API and downloads a supported Windows ZIP. It extracts the files under:

```text
vendor\fluidsynth
```

The application does not modify Windows PATH and does not require administrator access for this app-local setup.

Project: <https://github.com/FluidSynth/fluidsynth>

### Install GeneralUser GS

The application downloads `GeneralUser-GS.sf2` and its license from the official GeneralUser GS GitHub repository. Files are stored under:

```text
assets\soundfonts
```

Project: <https://github.com/mrbumpy409/GeneralUser-GS>

### Install Audiveris

On supported Windows systems, the app may invoke Windows Package Manager when the user explicitly requests Audiveris installation. Users may instead install Audiveris independently and select its location.

Project: <https://github.com/Audiveris/audiveris>

### Maple Leaf Rag sample

The sample helper downloads Mutopia's public-domain MIDI edition and writes source and checksum information beside the file.

Source page: <https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=23>

## Third-party licenses

MIDI Performer's PolyForm Noncommercial license applies to MIDI Performer code only. It does not replace or override licenses for:

- FluidSynth
- GeneralUser GS
- Audiveris
- FFmpeg
- Python packages
- SoundFonts selected by the user
- MIDI files
- PDF scores
- reference recordings
- Mutopia editions

Users and redistributors are responsible for preserving required notices and following each component's license.

## Music files and GitHub Issues

Do not upload copyrighted MIDI, score, or audio files to a public issue unless you have permission. Prefer a small synthetic MIDI or public-domain sample that reproduces the problem.

## Settings and logs

Local settings and runtime logs may contain file paths from the user's computer. They are excluded by `.gitignore`. Review logs before posting them publicly and remove information you do not want to share.
