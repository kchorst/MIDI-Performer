# MIDI Remixer v0.34.10

MIDI Remixer is the creative arranger in the MIDI Library Manager + MIDI Remixer product system. It consumes the authoritative Library maintained by MIDI Library Manager and turns Library-approved MIDI material into Guided mash ideas, Version Fusion ideas, Assisted recommendations, and Custom/Advanced remix work.

## Can I use it by itself?

**Remixer is independently launchable, but it is not independently self-sufficient for a new user.**

- If you have **no existing Library**, Remixer has no authoritative catalogue to recommend/remix from. Install/open MIDI Library Manager v0.39.3 first, add/restore MIDI sources, and build the Library.
- If you are a **returning user with an existing compatible Contract-4 / schema-15 Library**, Remixer can attach to or warm-start the compatible Library Engine even when the Library Manager program folder is not immediately beside it.
- Remixer still does **not** replace full Library management. Its Library selectors are bounded/read-only creative selectors, not a second Library Manager.

For normal users, especially first-time installation, **install both products and keep Library Manager available**.

## Release compatibility

| Item | Release value |
| --- | --- |
| MIDI Remixer | **v0.34.10** |
| Matched MIDI Library Manager | **v0.39.3** |
| Library Engine | **0.36.3** |
| Library Contract | **4** |
| Library schema | **15** |
| Supported OS | **Windows 10/11, 64-bit** |
| Supported Python | **3.11, 3.12, or 3.13, 64-bit** |
| License | **PolyForm Noncommercial 1.0.0** |

## Installation

For complete Remixer installation and the standalone/paired distinction, see **`INSTALL_GUIDE.md`**.

Recommended short version:

1. Install/extract **MIDI Library Manager v0.39.3** and create or restore the authoritative Library.
2. Extract MIDI Remixer v0.34.10 to a writable folder; sibling folders are simplest.
3. Double-click **`Launch MIDI Remixer.bat`**.
4. The launcher reuses `%LOCALAPPDATA%\MIDI Remixer\PythonEnv`; it prepares the exact dependencies only if required.
5. Remixer attaches to the compatible Library/Engine and reuses committed catalogue, analysis, family, eligibility, and recommendation data.

Pinned runtime packages: **PySide6 6.8.3, mido 1.3.3, rapidfuzz 3.14.3, openpyxl 3.1.5**.

For audio: **FluidSynth + a `.sf2`/`.sf3` SoundFont**. **FFmpeg** is additionally required for MP3 output.

## Product boundary

**MIDI Library Manager owns:** source folders, Fast Scan, Deep Analysis, Library migration, lifecycle classification, composition-family identity, Remix Eligibility, quarantine/recovery, persistent preparation, full Library file maintenance, and Library backup/restore.

**MIDI Remixer owns:** Guided Remix, Version Fusion, Assisted Studio, Custom/Advanced work, remix sessions/projects, Trials/Takes, Remixer audition/playback, and export.

Remixer does not manufacture an empty Library on a fresh profile and does not silently override Library lifecycle/eligibility truth.

## Working modes

1. **Guided Remix** — high-confidence mash ideas between distinct eligible composition families.
2. **Version Fusion** — complementary alternate arrangements/versions of the same composition.
3. **Assisted Studio** — more control over anchors, criteria, previews, shaping, and alternatives.
4. **Custom / Advanced** — direct creative control while respecting Library admission.

Normal mash discovery excludes exact copies and same-composition aliases. Version Fusion is intentionally separate for materially different alternate arrangements. Session-wide More Ideas exclusions prevent renamed versions of one song from repeatedly occupying recommendation slots.

## Full Library vs Remixer selectors

Use **Open MIDI Library Manager…** / **Open Full MIDI Library…** for scanning, analysis, full browsing, eligibility/review, quarantine/recovery, file actions, backups, and Library maintenance.

Remixer selection dialogs are bounded read-only creative selectors. They can audition and choose Library-approved sources but do not become a second Library-management application.

## Documentation

- **`INSTALL_GUIDE.md`** — paired installation, returning-user Remixer-only installation, dependencies, and Library requirements.
- **`USER_GUIDE.md`** — complete creative workflow guide.

## Repository contents

This public package intentionally contains only runtime/application source, required data/resources, the runtime reference-import helper, launch/install files, documentation, dependency manifests, `.gitignore`, and license. Qualification harnesses, tests, results, logs, build intermediates, caches, and local user data are excluded.

## License

MIDI Remixer is provided under the **PolyForm Noncommercial License 1.0.0**. See **`LICENSE.md`** for the complete terms and required notice:

`Required Notice: Copyright © 2026 Kevin Horst.`

The license permits noncommercial purposes as defined in its terms. Commercial use is not granted by this license. Third-party Python packages, FluidSynth, FFmpeg, SoundFonts, and user-provided MIDI/media remain under their own licenses.
