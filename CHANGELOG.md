# Changelog

## 0.7.0 - 2026-07-19

First public Early Access source release.

### Added

- Public version identity, About information, tagline, and guiding principle
- First-run playback setup with separate FluidSynth and instrument-library actions
- App-local FluidSynth download from the official release feed
- GeneralUser GS recommended-library download with license preservation
- Instrument Library Manager and main-screen library selector
- README, Quick Start, User Guide, FAQ, troubleshooting, release notes, GitHub publishing, licensing, and Ombra smoke-test documentation
- GitHub issue and pull-request templates
- PolyForm Noncommercial 1.0.0 license and third-party notice
- Maple Leaf Rag public-domain sample guidance and download helper

### Included capabilities

- MIDI playback through FluidSynth and a selected `.sf2` or `.sf3` instrument library
- Track Mixer with instrument overrides, balance, register, solo, and mute
- Musical Interpretation Engine
- Immutable Original and numbered Takes
- PDF score intake, optional Audiveris analysis, and alignment workspace
- Reference-audio intake and guidance when analysis is available
- Project save and reopen
- MIDI, WAV, and MP3 export subject to required dependencies

### First-run and playback corrections

- Replaced ambiguous combined audio setup with **Install FluidSynth**, **Use Existing**, **Install GeneralUser GS**, and **Choose Existing**
- Test Playback plays before asking for feedback
- Feedback remains inside the setup window through **Yes, I heard it**, **No sound**, and **Play Again**
- Failed Windows audio routes can advance to another supported route
- Changing FluidSynth or the active instrument library clears the previous playback-verification state
- External process checks suppress Windows command windows
- Audiveris installation controls are hidden when Audiveris is already detected

### Documentation and cleanup

- Standardized the public terminology around Playback Engine, Instrument Library, Score Analysis, Original, and Takes
- Removed stale setup directions and internal milestone labels from public-facing text
- Updated verification counts to 22 automated tests
- Added GitHub publication instructions and release text

### Verification

- Complete Python source compilation passed
- 22 automated tests passed, including offscreen Qt construction and inline first-run playback-test flow
- Private Ombra MIDI, PDF, and MP3 inputs passed static parsing and guidance checks
- Audible Windows playback, actual downloads, save/reopen, and export remain part of the target-machine smoke test
