# Release Notes

## MIDI Performer 0.7.0 Early Access

**Release date:** July 2026  
**Release type:** First public source release

## Release purpose

Version 0.7.0 presents the complete MIDI Performer workflow in an Early Access form:

- preserve Original as the immutable comparison baseline
- improve the sound through instrument-library choice, instrumentation, register, and balance
- create and compare numbered Takes
- apply the Musical Interpretation Engine
- add optional score and reference-performance guidance
- save projects and export selected results

## First-run playback

FluidSynth and GeneralUser GS are not bundled in the source archive. When a playback component is missing, the first-run setup presents explicit actions:

- **Install FluidSynth** or **Use Existing**
- **Install GeneralUser GS** or **Choose Existing**

The app-local FluidSynth setup uses the official FluidSynth GitHub release feed. The recommended library setup uses the official GeneralUser GS repository and stores the library license beside the downloaded file.

The playback test plays a chord before requesting feedback. Feedback remains inside the setup window through **Yes, I heard it**, **No sound**, and **Play Again**.

## Instrument libraries

The active instrument library appears on the main screen. The Instrument Library Manager can:

- install GeneralUser GS
- add an existing `.sf2` or `.sf3` file
- discover recent and application-local libraries
- switch the active library

This release treats library choice as a musical decision because it can change the result substantially before interpretation is applied.

## Musical workflow

- Original remains available for direct comparison.
- Numbered Takes store distinct musical ideas.
- Track Mixer controls instrument programs, register, volume, pan, solo, and mute.
- The interpretation controls provide Light, Medium, and Enhanced strength plus Expression and Timing choices.
- PDF scores and reference audio can guide new Takes when analysis succeeds and the user enables the relevant source.
- Alignment views support focused 4, 8, and 16 bar inspection.

## Samples and licensing

The repository does not distribute private Ombra files. It includes a helper that downloads Mutopia's public-domain MIDI edition of *Maple Leaf Rag* and records the source information.

Third-party tools and libraries retain their own licenses.

## Verification status

Completed before packaging:

- complete Python compilation
- 22 automated tests
- offscreen MainWindow and first-run-dialog construction
- simulated inline playback-test state flow
- fresh archive extraction and checksum verification
- static parsing and guidance checks with private Ombra MIDI, PDF, and MP3 inputs

Still required before calling the public release accepted on the target machine:

- clean first-run FluidSynth download or selection
- clean GeneralUser GS download or library selection
- audible Test Playback confirmation
- application restart and settings-persistence check
- complete Ombra smoke test
- save and reopen check
- WAV export listening check

See [VERIFICATION.md](../VERIFICATION.md) and [OmbraSmokeTest.md](OmbraSmokeTest.md).
