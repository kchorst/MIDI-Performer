# Security Policy

## Supported version

Security reports are currently accepted for MIDI Performer 0.7.x Early Access.

## Reporting a vulnerability

Do not publish a vulnerability that could expose local files, execute unintended commands, or compromise downloaded components before the maintainer has had a reasonable opportunity to investigate it.

Use GitHub's private security advisory feature when it is enabled. If private reporting is not available, open a minimal issue asking for a private contact channel without including exploit details.

Include:

- affected version
- operating system and Python version
- exact component or file
- reproduction steps
- expected and actual behavior
- whether untrusted MIDI, PDF, audio, archive, or path input is involved

## Download security

The first-run setup downloads only from fixed official HTTPS sources defined in the source code. Downloaded FluidSynth archives are checked for unsafe extraction paths. GeneralUser GS is checked for a usable file size and its license is downloaded beside it.

Users should still review third-party licenses and obtain releases only from official sources.
