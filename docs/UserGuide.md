# The MIDI Performer Guide

## From MIDI to Musical Performance

**Version 0.7.0 Early Access**  
**July 2026**

> The purpose of MIDI Performer is not to replace musical judgment, but to assist it. Every Take is an invitation to listen, compare, and choose.

## 1. The performance-building approach

MIDI Performer treats a performance as two connected questions:

1. **How does it sound?** Instrument library, instrumentation, register, volume, pan, and balance.
2. **How is it played?** Timing, dynamics, phrasing, score guidance, and reference-performance guidance.

Many disappointing MIDI files can be improved before interpretation controls are touched. A different instrument library, a better instrument assignment, a more convincing register, or a better balance may reveal that the MIDI itself is usable.

The final objective is always the performance the user prefers.

## 2. First-run setup

### Playback Engine: FluidSynth

FluidSynth converts MIDI events into audible sound by using a SoundFont. MIDI Performer can either download a supported Windows FluidSynth release into its own folder or use an existing `fluidsynth.exe` wherever it is already installed.

The one-click setup does not require administrator access and does not change Windows PATH. If automatic download is blocked by a firewall, network policy, or GitHub outage, choose **Use Existing**.

### Instrument Library: SoundFont

A SoundFont is presented as an instrument library because it supplies the actual instrument sounds. MIDI Performer can download GeneralUser GS as the recommended starting library, or the user can select another `.sf2` or `.sf3` file.

A confirmed path means that a file was located and passed basic validation. It does not prove that the library sounds appropriate for every piece. Listen and compare.

### Score Analysis: Audiveris

Audiveris is optional. It may improve recognition of suitable scanned or image-based PDF scores. MIDI Performer can use an automatically detected installation, a path selected by the user, or an installation initiated through the setup controls when available.

### Test Playback

After FluidSynth and an instrument library are ready, press **Test Playback**. The chord plays first. Feedback then appears inside the same setup window.

- **Yes, I heard it** saves the verified audio route.
- **No sound** tries another supported Windows route.
- **Play Again** repeats the test.

## 3. The seven building blocks

### MIDI

The MIDI supplies notes, channels, timing, controllers, and program information. It may be accurate, incomplete, mechanically timed, badly balanced, or assigned to instruments that render poorly in the selected library.

### Instrument Library

The instrument library supplies the audible instruments. Two libraries can render the same MIDI very differently. Library choice is therefore part of the musical result, not merely a technical dependency.

The active library appears on the main screen. The Instrument Library Manager discovers recent and application-local `.sf2` and `.sf3` files and lets the user add or select another library.

### Instrumentation

Track Mixer lets the user keep or replace loaded instrument programs. Register controls are intended to place a selected library instrument where it sounds convincing. They are not an invitation to transpose music casually.

### Balance

Per-track volume, pan, solo, and mute help establish foreground, accompaniment, and ensemble balance. The global volume control changes listening and rendering level without redefining the musical relationships between tracks.

### Score and reference performance

The score provides written evidence such as dynamics, articulations, tempo words, and structural cues when analysis can extract them. Reference audio provides measured expressive evidence such as loudness movement, attacks, and motion. Both sources are optional.

### Musical Interpretation Engine

The engine reshapes MIDI timing and velocity using the selected interpretation controls and any enabled source guidance. There is one engine. It works with the evidence available.

### Takes

Original is the untouched baseline. Numbered Takes preserve alternative musical ideas so the user can compare rather than overwrite.

## 4. Main-screen workflow

### Load MIDI

Open a Standard MIDI File and allow playback preparation to finish. The MIDI summary identifies useful information such as tracks, programs, duration, meter, and tempo when available.

### Instrument Library selector

Use the main selector to change the current library without entering Settings. Choose **Manage Instrument Libraries** to add an existing file or install GeneralUser GS.

Changing the library changes the rendered sound. It does not rewrite the notes in Original.

### Playback controls

Use Play, Pause, Stop, seek, volume, and tempo to audition the selected row. The transport label identifies whether Original or a numbered Take is selected.

### Track Mixer

Use Track Mixer for instrument programs, register, per-track volume, pan, solo, and mute. Mixer changes are musical decisions and become part of newly created numbered Takes.

## 5. Interpretation controls

### Interpretation Strength

- **Light** adds subtle timing and velocity changes and is the safest first audition.
- **Medium** makes phrasing and shaping clearer.
- **Enhanced** applies the strongest reshaping and should be judged by listening rather than assumed to be better.

### Expression

- **None** adds no new dynamic expression beyond the source MIDI.
- **Light** adds small velocity shaping.
- **Medium** adds balanced accents and phrase movement.
- **Strong** adds more obvious emphasis.
- **Very Strong** creates the greatest dynamic contrast and may be unsuitable for some material.

### Timing

- **Straight** stays close to the source timing grid.
- **Natural** adds small timing movement.
- **Lilt** adds a lightly lifted or swing-like feel where appropriate.
- **Laid Back** places events slightly behind the beat for a relaxed feel.

Interpretation controls remain active when score or audio guidance is enabled. Score and audio have separate influence strengths.

## 6. Original and numbered Takes

Original is loaded once for each MIDI and remains immutable. Instrumentation, mixer, tempo, interpretation, score, and audio decisions are evaluated through numbered Takes.

A useful cycle is:

**Interpret. Audition. Reflect. Keep. Compare.**

Play the pending numbered row to create and hear a new Take. Each Take stores its settings and may be noted, favorited, marked Best, duplicated, moved, deleted, or checked for export. Original is never deleted through Take management.

Use Take notes to describe the musical idea, not only the control values. `Quieter accompaniment` is more useful than `volume 82`.

## 7. Recommended rehearsal-like workflow

### Listen

Load the MIDI and play Original. Identify the most obvious problem. Do not begin by changing every control.

### Build the sound

Choose an instrument library. Open Track Mixer. Correct unsuitable programs, register, levels, and pan. Audition again.

### Interpret

Begin with Light interpretation, Light or Medium expression, and Natural timing. Create a numbered Take and listen from the same passage used to judge Original.

### Add the score

Attach a PDF and analyze it. A PDF alone does not alter a Take. Score guidance affects a new Take only when usable guidance was extracted and **Use score** is enabled. If analysis cannot extract guidance, the PDF may still serve as a visual or alignment reference.

### Add a reference performance

Attach a local audio file and analyze it. Enable **Use audio** to apply derived guidance to a new Take. MIDI Performer analyzes the recording for expressive guidance. It does not learn the recording, clone it, or guarantee a match.

### Compare

Return to Original frequently. Compare only a few clear alternatives at a time.

### Choose and export

Mark a preferred Take as Best or check several Takes for export. The strongest settings are not automatically the best settings.

## 8. Score intake

### Supported role of a PDF

A PDF can serve as:

- a visual score reference
- a source of embedded text and markings
- an input to optional optical music recognition
- a focused region for alignment inspection

Successful display is not the same as successful guidance extraction. Always read the analysis status.

### Use score

Score guidance requires all of the following:

1. a score is loaded
2. analysis is run
3. usable guidance is reported
4. **Use score** is enabled
5. a new numbered Take is created

### Page focus

Use all pages for overview or select a page or page range for a difficult passage. Focused analysis can reduce irrelevant material and make the alignment preview easier to inspect.

## 9. Alignment workspace

The 4, 8, and 16 bar views are inspection windows.

- Use 4 bars for detail.
- Use 8 bars for phrase context.
- Use 16 bars for overview.

The workspace can show the MIDI representation, score preview when available, and reference-audio information. It helps the user judge correspondence. It does not imply that every symbol or note has been perfectly recognized.

## 10. Reference audio

Use a clean local recording with limited noise and a reasonably stable relationship to the MIDI. A different edition, cuts, repeats, or a highly free performance can reduce alignment usefulness.

MIDI Performer derives guidance from measurable features such as dynamics, attack, pacing, and motion proxies. It does not copy the recording into the MIDI.

Compressed formats may require FFmpeg. WAV is the simplest diagnostic format when compressed audio cannot be analyzed.

Users remain responsible for permission to use their reference files.

## 11. Projects

Save projects to preserve source references, Takes, favorites, Best status, notes, settings, and analysis state. Reopen the project and verify that local source paths still exist.

Moving a MIDI, PDF, audio file, FluidSynth installation, or instrument library after saving can break the stored path. Use the relevant Choose Existing or file-selection control to reconnect it.

## 12. Export

### MIDI

Exports the selected Take as MIDI.

### WAV

Requires working FluidSynth rendering and an instrument library.

### MP3

Requires FFmpeg in addition to working WAV rendering.

Use **Export checked** for several Takes or **Export Best** for the single keeper. Listen to each rendered file from the beginning and confirm that there is no click, sharp transient, missing attack, or unexpected silence.

## 13. Maple Leaf Rag exercise

The sample helper downloads Mutopia's public-domain MIDI edition of Scott Joplin's *Maple Leaf Rag*. Joplin wrote, "It is never right to play Ragtime fast."

Begin by listening to the downloaded MIDI and adjusting tempo before applying interpretation controls. This teaches an important principle: improvement does not always mean adding more expression. Sometimes it means respecting the character and pace of the music.

## 14. Ombra mai fu private smoke exercise

A flawed opening is useful for judging whether instrument-library, instrumentation, balance, register, and interpretation changes are audible. The project does not distribute the development Ombra files. Users who already have lawful copies can follow [OmbraSmokeTest.md](OmbraSmokeTest.md).

## 15. Current limitations

- PDF results depend heavily on source quality and optional OMR.
- Score and reference-audio guidance are interpretive aids, not full note-level reconstruction.
- Different Windows audio devices may require different FluidSynth routes.
- Automatic FluidSynth and GeneralUser GS setup requires network access and write permission in the application folder.
- MP3 features depend on FFmpeg.
- This is an Early Access source release and has not yet been presented as a packaged installer.

## 16. Guiding principle

The final performance is always the musician's choice.

**Human Interpretation. Software Implementation.**
