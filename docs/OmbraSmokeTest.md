# Ombra mai fu Smoke Test

Use this checklist with lawful local copies of an Ombra MIDI, PDF, and MP3. These files are not included in the repository.

## Test record

Record:

- date
- Windows version
- Python version
- FluidSynth version and location type
- instrument-library filename
- Audiveris status
- FFmpeg status

## Clean first run

1. Extract the final candidate into a new writable folder.
2. Create the Python environment and install requirements.
3. Launch `MIDI Performer.pyw` with the virtual environment's `pythonw.exe`.
4. Confirm the Welcome setup appears when FluidSynth or the instrument library is absent.
5. Use **Install FluidSynth** or **Use Existing**.
6. Use **Install GeneralUser GS** or **Choose Existing**.
7. Confirm detected Audiveris does not continue to show an unnecessary install action.
8. Press **Test Playback**.
9. Confirm the chord plays before the feedback controls appear.
10. Select **Yes, I heard it**.
11. Close and restart the app. Confirm paths and the verified route persist.

## Ombra sequence

1. Load the Ombra MIDI. Confirm the filename and MIDI summary update.
2. Play Original. Confirm sound, transport movement, pause, stop reset, seek, volume, and tempo.
3. Change the instrument library. Confirm the opening changes audibly and the selector updates.
4. Open Track Mixer. Change one clearly audible instrument, register, or level. Apply it and create a numbered Take.
5. Confirm Original remains available and can be selected again.
6. Create a Light, Light, Natural Take. Compare the opening directly with Original.
7. Load the Ombra PDF. Select a useful page or page range. Analyze it and record whether usable guidance was extracted.
8. Open Align Sources. Check 4, 8, and 16 bar views. Confirm the selected score region appears and the dialog remains usable on screen.
9. Enable **Use score** only when guidance is available. Create a new Take and confirm its note records score involvement.
10. Load the Ombra MP3. Analyze it. Confirm waveform or analysis information appears.
11. Enable **Use audio**. Create another Take and confirm its note records audio involvement.
12. Enable both score and audio. Create a combined-guidance Take.
13. Compare Original and all new Takes. Confirm each Play control plays the selected row rather than a stale MIDI.
14. Add a Take note, mark a favorite, and mark one Take Best.
15. Save the project, close the application, reopen it, and load the project.
16. Confirm sources, Takes, notes, favorite, Best, settings, and file references are restored.
17. Export one checked Take as MIDI and WAV. Export MP3 only when FFmpeg is confirmed.
18. Listen from the first sample. Record any click, sharp artifact, missing attack, unexpected silence, or wrong instrument.
19. Confirm Original was never overwritten or deleted.
20. Review `logs\audio_diagnostics.log` and any console output for tracebacks.

## Acceptance

The candidate passes only when the complete sequence succeeds or every remaining defect is documented as a known Early Access limitation with a GitHub issue.
