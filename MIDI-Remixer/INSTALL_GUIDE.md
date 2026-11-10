# MIDI Remixer v0.34.10 — Installation Guide

## What this product is — and is not

MIDI Remixer is the creative arranger for the MIDI Performer product system. It uses the authoritative Library created and maintained by MIDI Library Manager.

**MIDI Remixer can be launched as its own application, but it is not a full standalone Library product for a new user.**

- A **new user with no existing Library** must install/use MIDI Library Manager first to add MIDI source folders, scan them, perform Library analysis/review, and create the authoritative catalogue.
- A **returning user who already has a compatible Library Contract 4 / schema 15 Library** can launch Remixer and attach to/warm-start the compatible Library Engine even if the Library Manager program folder is not currently beside it.
- However, Remixer still does **not** provide the full Library-management functions: it does not add source roots, run the normal full Fast Scan/Deep Analysis management workflow, migrate/manage the Library, quarantine/restore files, perform full Library file maintenance, or provide Library backup/restore as a replacement for MIDI Library Manager.

For normal use, especially for a new installation, **install both products and keep MIDI Library Manager available**.

## Matched release

| Component | Required/matched release |
| --- | --- |
| MIDI Remixer | v0.34.10 |
| MIDI Library Manager | v0.39.3 |
| Library Engine | 0.36.3 |
| Library Contract | 4 |
| Library schema | 15 |

## Supported system

- Windows 10 or Windows 11, 64-bit
- 64-bit Python **3.11, 3.12, or 3.13**
- A normal writable folder for application files
- Internet access during first dependency installation, unless the compatible shared environment already exists

## Required Python dependencies

The launcher installs/reuses the exact versions in `requirements.lock`:

| Package | Version | Purpose |
| --- | ---: | --- |
| PySide6 | 6.8.3 | Qt desktop interface |
| mido | 1.3.3 | MIDI parsing/data handling |
| rapidfuzz | 3.14.3 | Identity/text matching |
| openpyxl | 3.1.5 | Spreadsheet/reference-evidence import |

The shared private environment is:

`%LOCALAPPDATA%\MIDI Remixer\PythonEnv`

If MIDI Library Manager has already prepared this exact environment, Remixer reuses it. Do not install a duplicate copy of the Python dependencies for Remixer.

## Recommended install: Remixer with Library Manager

1. Install/extract **MIDI Library Manager v0.39.3**.
2. Install/extract **MIDI Remixer v0.34.10**. Sibling folders under the same parent are the simplest arrangement:

```text
MIDI Performer Suite\
├── MIDI-Library-Manager-v0.39.3\
└── MIDI-Remixer-v0.34.10\
```

3. Launch **`Launch MIDI Library Manager.bat`** first.
4. Create the authoritative Library with **Add Folder…**, or restore an existing Library backup.
5. Allow Fast Scan to catalogue the MIDI collection. For the complete Guided/Assisted/Fusion experience, allow the Library to complete the required Deep Analysis, composition-family reconciliation, eligibility, and recommendation-readiness preparation.
6. Launch **`Launch MIDI Remixer.bat`**.
7. Remixer should attach to the compatible Library/Engine. If it cannot locate Library Manager when you request full Library functions, use **Open MIDI Library Manager…** and select the Library Manager launcher.

## Remixer-only install for a returning user

A Remixer-only code-folder installation is useful only when the same Windows profile already contains a compatible established Library.

1. Extract MIDI Remixer v0.34.10.
2. Double-click **`Launch MIDI Remixer.bat`**.
3. Remixer checks for a compatible established Library/Engine and fails closed if none exists.
4. Creative workflows use the existing committed Library data.

If the Library has never been created on that Windows profile, Remixer will remain disconnected. Install/open MIDI Library Manager and create or restore the Library; Remixer deliberately does not manufacture an empty replacement Library.

## What Library access exists inside Remixer

Remixer includes **bounded, read-only creative selectors** for choosing/auditioning Library-approved MIDI sources. These selectors are not the full Library application.

Use MIDI Library Manager for:

- adding/removing source folders;
- Fast Scan and Deep Analysis management;
- composition-family/eligibility preparation and review;
- full Library browsing/maintenance;
- quarantine, restore, retry, rejection, and file actions;
- backups/restores and Library recovery.

Use MIDI Remixer for:

- Guided Remix;
- Version Fusion;
- Assisted Studio;
- Custom/Advanced remix work;
- Trials/Takes, audition, projects/sessions, and export.

## Audio and export setup

For creative MIDI recommendations alone, the Python dependencies are enough. For audio features:

- **FluidSynth** plus a General MIDI `.sf2`/`.sf3` SoundFont are required for audition/playback/audio rendering.
- **FFmpeg** is additionally required for MP3 output.
- WAV rendering does not require FFmpeg once FluidSynth/SoundFont rendering is configured.

## Finding Library Manager

Remixer can discover a Library Manager through a remembered installation, a compatible Engine source location, sibling/co-located installs, the `MIDI_LIBRARY_MANAGER_HOME` environment variable, or a launcher selected by the user.

Keeping the matched Library Manager folder beside the Remixer folder is the easiest configuration for normal users.

## Updating

Do not mix files from different Remixer releases in the same folder. Extract a new release to a clean folder. The Remixer code folder is separate from the persistent authoritative Library data under the Windows user profile.

Keep the matched Library Manager v0.39.3 / Contract-4 / schema-15 installation available for full Library maintenance.

## Troubleshooting installation

- **Remixer opens but says no Library is available:** open MIDI Library Manager and create/restore the Library.
- **Library exists but full Library cannot be opened:** locate `Launch MIDI Library Manager.bat` through Open MIDI Library Manager… or configure `MIDI_LIBRARY_MANAGER_HOME`.
- **Engine incompatible:** use the matched v0.39.3 Library release for this Remixer.
- **No playback audio:** configure FluidSynth and a SoundFont.
- **MP3 export unavailable:** configure FFmpeg in addition to FluidSynth/SoundFont.
- **Dependency installation fails:** verify a supported Python and internet access, then rerun `Install Requirements.bat`.

## License

MIDI Remixer v0.34.10 is provided under the **PolyForm Noncommercial License 1.0.0**. See `LICENSE.md` for the complete terms and required notice.

The license permits the software for noncommercial purposes as defined by those terms. Commercial use is not granted by this license. Third-party dependencies, FluidSynth, FFmpeg, SoundFonts, and user-provided MIDI/media remain under their own licenses.
