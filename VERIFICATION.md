# Verification Report

**Release:** MIDI Performer 0.7.0 Early Access  
**Build date:** 2026-07-19

## Source verification

- Complete source compilation with `python -m compileall`: passed
- Automated test suite: **22 passed**
- MainWindow and first-run dialog construction in offscreen Qt: passed
- Inline first-run playback-test state flow: passed with a simulated playback backend
- Fresh documentation candidate extraction and checksum verification: passed

## First-run behavior verified in code

- Missing FluidSynth presents **Install FluidSynth** and **Use Existing**
- Missing instrument library presents **Install GeneralUser GS** and **Choose Existing**
- GeneralUser GS is selected after successful recommended-library setup
- Audiveris is optional, and its install button is hidden when Audiveris is detected
- **Test Playback** plays before asking for feedback
- Feedback remains inside the first-run window with **Yes, I heard it**, **No sound**, and **Play Again**
- Choosing **No sound** advances to another supported Windows audio route
- Changing FluidSynth or the instrument library invalidates previous playback verification
- External version checks and FluidSynth startup use no-console Windows process flags

## Ombra input verification

The private Ombra files supplied for release preparation are not included in the package.

- MIDI: 6 tracks, 645 notes, 146.18 seconds, 3/4, approximately 65 BPM, programs 48 and 60
- PDF: rendered successfully as 2 pages; 24 embedded dynamic markings were detected; PDF score guidance reported active
- MP3: 177.44 seconds, 22050 Hz, mono; FFmpeg produced a 512-point contour; reference-audio guidance reported active

## Documentation and repository verification

- All local Markdown links: passed
- GitHub issue-template YAML parsing: passed
- Private media, downloaded SoundFonts, downloaded binaries, logs, and local settings excluded from the source package
- GitHub publishing guide and release text included

## Still required on the target Windows computer

- install or select FluidSynth through the final first-run dialog
- install or select an instrument library
- confirm the chord is audible and press **Yes, I heard it**
- restart and verify saved selections
- complete `docs/OmbraSmokeTest.md`
- listen to playback, test transport, save and reopen a project, and verify exported audio

This report does not claim that the entire graphical workflow and audible Windows output have passed the final target-machine acceptance test.
