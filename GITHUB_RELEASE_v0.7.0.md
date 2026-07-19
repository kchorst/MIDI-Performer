# MIDI Performer v0.7.0 Early Access

**Transforming MIDI into Musical Performance**

> **Human Interpretation. Software Implementation.**

This is the first public source release of MIDI Performer.

## Highlights

- Immutable Original and numbered Takes
- Main-screen Instrument Library selection and manager
- App-local FluidSynth setup or selection of an existing installation
- Recommended GeneralUser GS setup or selection of another `.sf2` or `.sf3` library
- Track Mixer for instrument programs, register, volume, pan, solo, and mute
- Musical Interpretation Engine with strength, Expression, and Timing controls
- Optional PDF score guidance and Audiveris support
- Optional reference-performance guidance
- 4, 8, and 16 bar alignment inspection
- Project save and reopen
- MIDI, WAV, and optional MP3 export

## Important release status

This is an Early Access source release, not a packaged installer. It is prepared primarily for Windows 10 and Windows 11 with Python 3.11 or 3.12.

FluidSynth and GeneralUser GS are not bundled in the source ZIP. The first-run setup can download them from their official projects into the MIDI Performer folder, or the user can select existing installations and instrument libraries.

## Start here

1. Read `docs/QuickStart.md`.
2. Complete first-run playback setup.
3. Confirm **Test Playback** is audible.
4. Load a MIDI and listen to Original.
5. Choose the instrument library and review Track Mixer before applying interpretation.

## Samples

The repository includes a helper for Mutopia's public-domain MIDI edition of Scott Joplin's *Maple Leaf Rag*. Private Ombra development files are not included.

## Feedback

Please open one GitHub Issue per reproducible problem. Include exact steps, Windows and Python versions, FluidSynth version, instrument-library filename, and relevant diagnostics. Do not upload copyrighted music or score files without permission.

## License

MIDI Performer uses the PolyForm Noncommercial License 1.0.0. Third-party components retain their own licenses.
