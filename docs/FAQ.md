# FAQ

## Is MIDI Performer a Windows application?

Version 0.7.0 is a source release prepared primarily for Windows 10 and Windows 11. Other platforms are not yet part of the public release claim.

## Is there an installer?

No. Version 0.7.0 is launched from Python source. It can install app-local FluidSynth and GeneralUser GS from inside the first-run setup, but that is not the same as a MIDI Performer installer.

## Which button installs FluidSynth?

Use **Install FluidSynth** beside Playback Engine. Use **Use Existing** when FluidSynth is already installed somewhere else.

## What is an instrument library?

It is a SoundFont file, normally `.sf2` or `.sf3`, that supplies the instrument sounds used by FluidSynth.

## Why is GeneralUser GS recommended?

It is a compact General MIDI and GS-compatible library that provides a stronger first experience than the previous fallback. It is only a starting recommendation. Users should compare libraries and choose the sound they prefer.

## Does changing the instrument library change Original?

It changes how Original is rendered, not the MIDI notes stored in the source file. Instrument-library choice is part of the playback and performance environment.

## Does loading a PDF change the performance?

No. Analyze the PDF, confirm that usable guidance was extracted, enable **Use score**, and generate a new Take. Otherwise the PDF is only a source or visual alignment reference.

## Does reference audio get copied into my MIDI?

No. MIDI Performer analyzes selected expressive features and derives guidance. It does not embed the recording into MIDI.

## Does the software learn or match a performer?

Those terms overstate the current behavior. Version 0.7.0 derives expressive guidance from the reference and applies it through the interpretation workflow.

## Why is Original different from a Take made with mild settings?

Original is the untouched loaded MIDI. A numbered Take passes through the current performance settings and variation process, even when interpretation controls are mild.

## Why can a new instrument library make a large difference?

The library supplies the actual samples and synthesis behavior. MIDI data alone does not determine sound quality.

## Why does PDF analysis vary?

PDFs may contain vector notation, images, scans, unusual engraving, or no machine-readable musical content. Optional OMR can help but does not guarantee complete recognition.

## Is Audiveris required?

No. It is optional and is most relevant when optical music recognition is needed for scanned or image-based scores.

## Is FFmpeg required?

Not for basic MIDI playback or MIDI export. It is needed for MP3 input and MP3 export features.

## Does MIDI Performer upload my music?

Version 0.7.0 processes MIDI, score, and reference files locally. It does not include analytics or a cloud account. Network access is used only for user-initiated component or sample downloads described in the documentation.

## Can I redistribute my downloaded SoundFonts or recordings with the project?

Only when their licenses permit it. MIDI Performer does not grant rights to third-party music, recordings, scores, SoundFonts, or tools.

## Where should bugs be reported?

Use GitHub Issues. Include reproducible steps and diagnostics, but do not upload files you are not permitted to share.
