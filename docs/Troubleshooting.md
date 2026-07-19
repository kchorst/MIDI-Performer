# Troubleshooting

**MIDI Performer 0.7.0 Early Access**

Start with the visible symptom, then follow the smallest relevant section.

## The application does not start

Open PowerShell in the repository folder and run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m compileall -q app core tests main.py
.\.venv\Scripts\python.exe main.py
```

Use Python 3.11 or 3.12. The console launch is intended for diagnostics and may show a traceback that is hidden by the normal `.pyw` launch.

## The Welcome setup does not appear

The setup appears only when FluidSynth or an instrument library is missing. Open **Settings > File Locations** to review or change the same components manually.

## Which button installs FluidSynth?

Press **Install FluidSynth** beside Playback Engine.

Do not press a generic audio-setup button. Version 0.7.0 uses separate actions for FluidSynth and the instrument library.

## FluidSynth download fails

Possible causes include a blocked network connection, GitHub outage, antivirus inspection, or lack of write permission in the MIDI Performer folder.

1. Move the extracted source to a writable folder under Documents or Desktop.
2. Retry **Install FluidSynth**.
3. If FluidSynth is already installed elsewhere, choose **Use Existing** and select `fluidsynth.exe`.
4. Do not move the existing installation merely to satisfy MIDI Performer.

Automatic setup stores FluidSynth under `vendor\fluidsynth` and does not modify Windows PATH.

## FluidSynth is installed but not found

1. Open **Settings > File Locations**.
2. Beside Playback Engine, choose **Use Existing** or **Assign Path**.
3. Select the actual `fluidsynth.exe` file.
4. Confirm that the status changes to Ready or Confirmed.
5. Press **Test Playback**.

## No instrument library is selected

Choose one of these:

- **Install GeneralUser GS** for the recommended starting library.
- **Choose Existing** for another `.sf2` or `.sf3` file.
- Open **Instrument Libraries** from the main screen to add or change libraries later.

## The selected instrument library sounds poor

This is not necessarily a MIDI problem.

1. Use the main **Instrument Library** selector.
2. Compare another `.sf2` or `.sf3` library with the same passage.
3. Recheck instruments and register in Track Mixer.
4. Recheck balance before increasing interpretation strength.

A different library can produce a major improvement without changing the notes.

## Test Playback appears to do nothing

In the current flow, the chord should play immediately after pressing **Test Playback**. Then the same window should show **Yes, I heard it**, **No sound**, and **Play Again**.

If no feedback controls appear:

1. wait several seconds for FluidSynth startup
2. click **Play Again** if visible
3. launch through `python main.py` to expose errors
4. review `logs\audio_diagnostics.log`
5. report the exact sequence and attach the text log

A blank information popup before the chord is not expected in the current release candidate.

## The chord plays only after closing a popup

That behavior belongs to an earlier build. Confirm that you extracted the latest release into a new folder rather than overwriting an older copy.

## I hear no test chord

1. Confirm FluidSynth and the instrument library both show Ready.
2. Confirm Windows output volume and the correct output device.
3. Press **No sound** so MIDI Performer can try another supported audio route.
4. Press **Play Again**.
5. Check whether FluidSynth appears in the Windows volume mixer while the chord is actively playing.
6. Review `logs\audio_diagnostics.log`.

## MIDI loads but there is no sound

1. Confirm Test Playback succeeds first.
2. Confirm an instrument library is selected on the main screen.
3. Select Original explicitly.
4. Wait for MIDI preparation to finish.
5. Press Play.
6. Check mute, solo, track volume, master volume, and tempo.
7. Try a small known-good MIDI.

If Test Playback works but MIDI playback does not, the problem is likely in MIDI preparation, routing, or the selected Take rather than FluidSynth installation.

## Audio works in another MIDI application but not here

That proves the general audio system and file can work, but not that MIDI Performer has selected an audible FluidSynth route. Use **Test Playback** and inspect the diagnostics log.

## A command window opens unexpectedly

Normal use through `MIDI Performer.pyw` should not open a command window for FluidSynth version checks or playback. Launching `main.py` intentionally opens a console for diagnostics.

If another command window appears during normal `.pyw` use, record the action that triggered it and report the issue.

## PDF is visible but does not influence a Take

Successful display is not the same as successful guidance extraction.

1. Analyze the score.
2. Read the analysis status.
3. Enable **Use score** only when guidance is available.
4. Create a new numbered Take.

Loading a PDF by itself does not change the performance.

## PDF preview is incomplete or badly segmented

- Try a clearer PDF.
- Select a smaller page range.
- Inspect 4 bars before 8 or 16.
- Confirm Audiveris is detected when OMR is needed.
- Accept that some PDFs will remain visual references only.

## Audiveris is already found but Install Audiveris is visible

Refresh the setup status or reopen Settings. In the current design, install controls should be hidden when Audiveris is detected. Report the displayed status and path if the button remains visible.

## Reference audio cannot be analyzed

- Confirm FFmpeg is available for MP3 and other compressed formats.
- Try WAV as a diagnostic format.
- Check for a damaged, extremely quiet, or unsupported file.
- Use a cleaner recording with fewer cuts, repeats, and audience noise.

## Export fails

- MIDI export requires a valid Take path.
- WAV export requires FluidSynth and an instrument library.
- MP3 export additionally requires FFmpeg.
- Confirm the output folder is writable.
- Open **Settings > Setup / Export Status** before retrying.

## A rendered file begins with a click or sharp artifact

Keep the exported file, record the Take number and format, and report the issue. Include whether the artifact also occurs during live playback. Do not upload copyrighted source material without permission.

## Original appears to change

Stop playback and select Original again. Mixer and interpretation decisions should become numbered Takes. Report a reproducible case if the loaded source MIDI itself is modified or if Original plays a numbered Take's settings.

## Settings do not persist

Confirm the application folder is writable. Settings are stored in:

```text
config\app_settings.json
```

Do not commit that local file to GitHub.

## How to report a useful bug

Include:

- version 0.7.0 Early Access
- exact steps
- expected and actual result
- Windows version
- Python version
- FluidSynth version and path type, such as app-local or existing
- instrument-library filename
- Audiveris and FFmpeg status when relevant
- `logs\audio_diagnostics.log`

Use a small synthetic or public-domain test file whenever possible.
