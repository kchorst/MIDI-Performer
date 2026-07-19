# Quick Start

**MIDI Performer 0.7.0 Early Access**

This guide takes a first-time Windows user from a fresh source download to a first numbered Take.

## 1. Extract the source

Extract the release into a normal writable folder. Do not run it from inside the ZIP. Avoid protected locations such as `C:\Program Files` because the application stores settings, work files, optional downloaded tools, and downloaded instrument libraries inside the project folder.

## 2. Install Python

Install Python 3.11 or 3.12. During Python setup, allow the Python launcher to be installed.

Open PowerShell in the MIDI Performer folder and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Start MIDI Performer

Launch without a console window:

```powershell
.\.venv\Scripts\pythonw.exe "MIDI Performer.pyw"
```

For troubleshooting, use:

```powershell
.\.venv\Scripts\python.exe main.py
```

## 4. Complete first-run playback setup

Audible playback requires FluidSynth and an instrument library.

### Playback Engine

When FluidSynth is missing, choose one of these:

- **Install FluidSynth** downloads a supported Windows release from the official FluidSynth GitHub project into `vendor\fluidsynth`.
- **Use Existing** lets you select an existing `fluidsynth.exe` without moving it.

### Instrument Library

When no instrument library is selected, choose one of these:

- **Install GeneralUser GS** downloads the recommended General MIDI SoundFont into `assets\soundfonts` and stores its license beside it.
- **Choose Existing** lets you select any usable `.sf2` or `.sf3` file.

GeneralUser GS is a recommended starting point, not a required artistic choice. A different library may suit a particular MIDI better.

### Score Analysis

Audiveris is optional. Use the detected installation, select an existing installation, install it when offered, or skip it for now.

### Test Playback

Press **Test Playback** after FluidSynth and an instrument library are ready. The chord plays immediately. Then choose:

- **Yes, I heard it**
- **No sound**
- **Play Again**

If you choose **No sound**, MIDI Performer tries another supported Windows audio route.

## 5. Load and listen to a MIDI

1. Open a MIDI file.
2. Select **Original** in Takes.
3. Press Play.
4. Listen before changing controls.

Identify the first obvious problem. It may be tempo, a poor instrument, an unsuitable register, or balance rather than interpretation.

## 6. Choose the sound

Use the **Instrument Library** selector on the main screen to compare available libraries. Open **Instrument Libraries** to add another `.sf2` or `.sf3` file.

Open **Track Mixer** and review:

- instrument programs
- register
- volume
- pan
- solo and mute

Apply only changes you can explain musically.

## 7. Create a first Take

A safe first experiment is:

- Interpretation Strength: **Light**
- Expression: **Light**
- Timing: **Natural**

Play the pending numbered row in Takes. MIDI Performer creates and plays that numbered Take. Compare it directly with Original.

Add a short note describing the musical idea, such as `lighter accompaniment` or `more natural phrase ending`.

## 8. Add optional score guidance

1. Load a PDF score.
2. Choose a page or page range when needed.
3. Press **Analyze Score**.
4. Read the analysis status.
5. Enable **Use score** only when useful guidance was extracted.
6. Generate a new numbered Take.

Loading a PDF alone does not change the performance.

## 9. Add optional reference-audio guidance

1. Load a local reference recording.
2. Analyze it.
3. Enable **Use audio**.
4. Select an influence strength.
5. Generate a new numbered Take.

MIDI Performer derives expressive guidance. It does not copy the recording or promise to match it.

## 10. Save and export

Save the project before closing the application. Mark one numbered Take as **Best** or check several Takes for export.

- MIDI export does not require audio rendering.
- WAV export requires working FluidSynth playback and an instrument library.
- MP3 features require FFmpeg.

## 11. Try Maple Leaf Rag

Run:

```powershell
.\.venv\Scripts\python.exe samples\MapleLeafRag\download_sample.py
```

Begin by adjusting tempo. Scott Joplin's warning that ragtime should not be played too fast makes this a useful critical-listening exercise.

## Next reading

- [User Guide](UserGuide.md)
- [Troubleshooting](Troubleshooting.md)
- [Downloads, Privacy, and Third-Party Components](DownloadsPrivacyAndLicensing.md)
