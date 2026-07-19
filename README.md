# MIDI Performer

**Version 0.7.0 Early Access**

**Transforming MIDI into Musical Performance**

> **Human Interpretation. Software Implementation.**

MIDI Performer helps musicians turn rigid, unbalanced, or poorly rendered MIDI files into performances worth comparing. It combines instrument-library choice, track-level sound decisions, a Musical Interpretation Engine, optional score guidance, optional reference-performance guidance, and numbered Takes.

The software proposes. The musician listens, compares, and chooses.

## Early Access status

Version 0.7.0 is the first public source release. It is prepared primarily for Windows 10 and Windows 11 source use. There is no installer in this release.

The application is functional, but Early Access means that users should expect rough edges, especially with unusual MIDI files, scanned PDF scores, external audio tools, and hardware-specific audio behavior. Please report reproducible problems through GitHub Issues.

## Why MIDI Performer exists

Many freely available MIDI files contain the correct notes but little interpretation. Improving them manually can require editing hundreds or thousands of events. MIDI Performer provides a faster way to explore musical alternatives without destroying the source.

A performance is built from two connected questions:

1. **How does it sound?** Instrument library, instrumentation, register, volume, pan, and balance.
2. **How is it played?** Timing, dynamics, phrasing, score guidance, and reference-performance guidance.

A better instrument library can improve a MIDI before any interpretation is applied. For that reason, MIDI Performer treats the SoundFont as an instrument library and places its selection on the main screen.

## What MIDI Performer does

- Preserves the loaded MIDI as an immutable **Original**.
- Plays MIDI through FluidSynth and a selected `.sf2` or `.sf3` instrument library.
- Lets the user change instrument programs, register, volume, pan, solo, and mute in Track Mixer.
- Creates numbered Takes through the Musical Interpretation Engine.
- Provides Light, Medium, and Enhanced interpretation strength.
- Provides Expression and Timing controls.
- Accepts PDF scores and can use extracted score guidance when available.
- Accepts local reference audio and can derive expressive guidance when analysis succeeds.
- Provides an alignment workspace for focused 4, 8, and 16 bar inspection.
- Saves and reopens projects.
- Exports selected Takes as MIDI and WAV, with MP3 available when FFmpeg is present.

## What MIDI Performer is not

- It is not a full digital audio workstation.
- It is not notation-authoring software.
- It does not guarantee complete recognition of every PDF score.
- It does not clone or copy a reference performance.
- It does not replace musical judgment.
- It does not intentionally overwrite the Original.

## Three operating modes

1. **MIDI + Musical Interpretation Engine**
2. **MIDI + Score + Musical Interpretation Engine**
3. **MIDI + Score + Reference Performance + Musical Interpretation Engine**

There is one interpretation engine. MIDI, score, and audio provide different evidence to the same process. Interpretation controls remain active in every mode. Score and audio each have their own enable control and influence strength.

## First run

MIDI Performer needs two items for audible playback:

- **Playback Engine:** FluidSynth
- **Instrument Library:** a SoundFont file such as GeneralUser GS

When either item is missing, the Welcome setup shows explicit choices:

- **Install FluidSynth** or **Use Existing**
- **Install GeneralUser GS** or **Choose Existing**

The in-app FluidSynth setup downloads a supported Windows release from the official FluidSynth GitHub project and stores it inside the MIDI Performer folder. It does not require administrator access and does not modify Windows PATH.

The recommended GeneralUser GS setup downloads the SoundFont and its license from the official GeneralUser GS repository. You may choose a different `.sf2` or `.sf3` library at any time.

After both items are ready, press **Test Playback**. The chord plays first. The same setup window then shows **Yes, I heard it**, **No sound**, and **Play Again**.

Audiveris is optional. It can improve optical music recognition for suitable PDF scores.

## Requirements

- Windows 10 or Windows 11 for the current release target
- Python 3.11 or 3.12
- Internet access for optional one-click FluidSynth and GeneralUser GS downloads
- Write access to the extracted MIDI Performer folder
- FluidSynth and an instrument library for audible playback
- FFmpeg for MP3 input and export features
- Audiveris for optional optical music recognition

## Install and launch from source

Extract the repository to a normal writable folder, such as a folder under Documents or Desktop. Then open PowerShell in that folder.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe "MIDI Performer.pyw"
```

For troubleshooting, launch with a visible console:

```powershell
.\.venv\Scripts\python.exe main.py
```

Read the [Quick Start](docs/QuickStart.md) before the first session.

## Recommended musical workflow

1. Load the MIDI and listen to Original.
2. Choose an instrument library.
3. Review instrumentation, register, balance, volume, and pan in Track Mixer.
4. Listen again.
5. Begin with Light interpretation, Light or Medium expression, and Natural timing.
6. Create a numbered Take.
7. Add and analyze a score when available.
8. Add and analyze a reference performance when available.
9. Compare numbered Takes with Original.
10. Mark the preferred Take as Best and export it.

## Included sample guidance

The repository includes a helper for Mutopia's public-domain MIDI edition of Scott Joplin's *Maple Leaf Rag*.

```powershell
.\.venv\Scripts\python.exe samples\MapleLeafRag\download_sample.py
```

Joplin wrote, "It is never right to play Ragtime fast." The sample is intended to encourage critical listening and tempo adjustment before applying more interpretation.

The Ombra mai fu files used during development are not included. Users who already have lawful local copies can follow the private [Ombra smoke test](docs/OmbraSmokeTest.md).

## Documentation

- [Quick Start](docs/QuickStart.md)
- [The MIDI Performer Guide](docs/UserGuide.md)
- [Troubleshooting](docs/Troubleshooting.md)
- [FAQ](docs/FAQ.md)
- [Release Notes](docs/ReleaseNotes.md)
- [GitHub Publishing Guide](docs/GitHubPublishing.md)
- [Downloads, Privacy, and Third-Party Components](docs/DownloadsPrivacyAndLicensing.md)
- [Ombra Smoke Test](docs/OmbraSmokeTest.md)
- [Changelog](CHANGELOG.md)

## Feedback

Use GitHub Issues for reproducible bugs and focused feature requests. Include:

- exact steps
- expected result
- actual result
- Windows and Python versions
- FluidSynth version
- instrument-library filename
- relevant diagnostics

Do not upload copyrighted MIDI, score, or audio files unless you have permission to share them. A small synthetic MIDI is preferred for public bug reports.

## License

MIDI Performer is distributed under the [PolyForm Noncommercial License 1.0.0](LICENSE).

FluidSynth, GeneralUser GS, Audiveris, FFmpeg, Mutopia files, and other third-party components retain their own licenses. See [NOTICE.md](NOTICE.md) and [Downloads, Privacy, and Third-Party Components](docs/DownloadsPrivacyAndLicensing.md).
