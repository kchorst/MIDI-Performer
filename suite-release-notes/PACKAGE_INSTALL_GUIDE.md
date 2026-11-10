# MIDI Performer Suite — Package Installation Guide

**Matched releases:** MIDI Library Manager v0.39.3 + MIDI Remixer v0.34.10  
**Platform:** Windows 10/11, 64-bit  
**Python:** 64-bit Python 3.11, 3.12, or 3.13  
**Library Contract:** 4 · **Library schema:** 15

## Which product do I need?

| What you want to do | Library Manager | Remixer | Important note |
| --- | --- | --- | --- |
| Catalogue, scan, analyze, search, review, maintain, back up, and audition a MIDI collection | **Required** | Optional | Library Manager is fully standalone. |
| Create Guided mash ideas, Version Fusion, Assisted/Custom remixes, Trials/Takes, and exports from a MIDI Library | **Required for a new user** | **Required** | Build/restore the Library first, then use Remixer. |
| Run Remixer on a Windows profile that already has a compatible established Library | Optional at launch | **Required** | Remixer can attach/warm-start the existing Engine, but full Library management is unavailable without Library Manager. |
| Full intended product experience | **Install** | **Install** | Recommended configuration. |

### The one-Library rule

There is one authoritative persistent MIDI Library. **MIDI Library Manager owns it.** Remixer reads/uses that committed Library state; it does not create a second catalogue.

Remixer contains read-only/bounded source selectors for creative workflows, but those selectors are **not** a full Library function. Adding source folders, Fast Scan, Deep Analysis management, lifecycle/eligibility review, quarantine/restore, Library repair, full file maintenance, and backup/restore belong to MIDI Library Manager.

## Recommended folder layout

Extract both product folders under one parent folder:

```text
MIDI Performer Suite\
├── PACKAGE_INSTALL_GUIDE.md
├── MIDI-Library-Manager-v0.39.3\
└── MIDI-Remixer-v0.34.10\
```

The products can live elsewhere, but sibling folders make discovery and user navigation simplest.

## Install both products — new user

### 1. Install Python

Install **64-bit Python 3.11, 3.12, or 3.13**. Python 3.14 is not the supported runtime for this release pair.

### 2. Extract both product folders

Do not merge the two application directories and do not copy files from one version into another. Keep each product in its own folder.

### 3. Launch MIDI Library Manager first

Open `MIDI-Library-Manager-v0.39.3` and double-click:

**`Launch MIDI Library Manager.bat`**

On first use, the launcher prepares the shared private Python environment if needed:

`%LOCALAPPDATA%\MIDI Remixer\PythonEnv`

The exact pinned dependencies are:

| Package | Version |
| --- | ---: |
| PySide6 | 6.8.3 |
| mido | 1.3.3 |
| rapidfuzz | 3.14.3 |
| openpyxl | 3.1.5 |

The first dependency installation normally needs internet access so `pip` can obtain the packages. If the exact shared environment already exists, it is reused.

### 4. Build or restore the authoritative Library

In MIDI Library Manager:

- choose **Add Folder…** and select the highest useful folder containing the MIDI collection; or
- choose **Restore Library Data…** if you already have a Library backup.

Fast Scan creates/reconciles the catalogue. Deep Analysis adds musical detail. Composition-family, eligibility, and recommendation-readiness preparation create the persistent state used by Guided/Assisted/Fusion workflows.

You can use Library Manager by itself at this point.

### 5. Launch MIDI Remixer

Open `MIDI-Remixer-v0.34.10` and double-click:

**`Launch MIDI Remixer.bat`**

Remixer reuses the same private Python environment and attaches to the compatible authoritative Library/Engine.

If Remixer cannot find the Library Manager when you request the full Library UI, choose **Open MIDI Library Manager…** and locate `Launch MIDI Library Manager.bat`.

## What happens if I install only one product?

### Library Manager only

**Fully supported.** Library Manager is useful on its own for MIDI collection management, analysis, review, playback, preparation, and backup/recovery. Remixer is not required.

### Remixer only — brand-new Windows profile

The program can launch, but **there is no useful authoritative Library yet**. Remixer deliberately stays disconnected rather than creating an empty replacement Library. Install/open Library Manager, create or restore the Library, then return to Remixer.

### Remixer only — returning user with an existing compatible Library

This is supported for creative use. Remixer can attach to/warm-start a compatible established Contract-4/schema-15 Library Engine and reuse the existing catalogue/analysis/eligibility/family state.

However, you will still need MIDI Library Manager whenever you want to add/change source folders, run/manage scans and detailed analysis, review eligibility/lifecycle, quarantine/restore files, perform full Library maintenance, or back up/restore the Library.

## Audio and export dependencies

These are separate from the four required Python packages:

- **FluidSynth** + a General MIDI `.sf2` or `.sf3` SoundFont — required for MIDI audition/playback and audio rendering.
- **FFmpeg** — additionally required for MP3 output from Remixer.

Library scanning/cataloguing and Remixer MIDI recommendation logic can operate without the audio stack.

## Shared data and upgrades

Application code folders are separate from persistent user data. The shared per-user suite data lives under:

`%APPDATA%\MIDI Remixer\`

Both products use the same authoritative Library data. Updating one application code folder does not mean creating a new Library.

For this release, keep the matched pair **Library v0.39.3 + Remixer v0.34.10**. Extract future releases to clean versioned directories instead of mixing files between releases.

## Uninstall / remove application code

Deleting an extracted application folder removes that code copy but does not automatically delete the per-user Library data under `%APPDATA%\MIDI Remixer\` or the shared Python environment under `%LOCALAPPDATA%\MIDI Remixer\PythonEnv`.

Do not delete persistent Library data unless you intentionally want to remove the catalogue/settings/backups for that Windows profile.

## More detailed guides

Inside each product folder:

- `INSTALL_GUIDE.md` — installation instructions for that individual product.
- `USER_GUIDE.md` — operating instructions after installation.
- `README.md` — product/release overview.
- `LICENSE.md` — software license.

## License

Both included products are provided under the **PolyForm Noncommercial License 1.0.0** with the required notice:

`Required Notice: Copyright © 2026 Kevin Horst.`

See each product's `LICENSE.md` for the complete terms. The license permits noncommercial purposes as defined by those terms; commercial use is not granted by this license. Third-party dependencies, FluidSynth, FFmpeg, SoundFonts, MIDI files, and other user content remain under their own applicable licenses.
