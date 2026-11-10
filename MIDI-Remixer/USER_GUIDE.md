# MIDI Remixer User Guide
**Version:** v0.34.10  
**Library compatibility:** MIDI Library Manager v0.39.3 · Library Contract 4 · schema 15

**Authoritative family prerequisite:** Guided, Assisted, and Version Fusion wait until Library v0.39.3 reports composition-family reconciliation complete. The Remixer never invents or persists competing family truth.
This guide explains the supported user workflow for MIDI Remixer. MIDI Library Manager is the authoritative Library owner; Remixer is the creative client.
## 1. Product architecture
The product system has one Library truth. **MIDI Library Manager** owns MIDI roots, Fast Scan, Detailed Analysis, lifecycle classification, Remix Eligibility, quarantine/recovery, persistence, backups/rebuilds, and the shared Library Engine. **MIDI Remixer** reads that committed state and owns creative recommendations, remix sessions, audition/playback, generated Trials/Takes, and export.
Remixer does not independently migrate, rebuild, scan, quarantine, delete from, or replace the authoritative Library. Closing Remixer does not stop an Engine that belongs to the Library system.
The working-mode order is **Guided Remix → Version Fusion → Assisted Studio → Custom / Advanced**.
## 2. Installation and startup
**Standalone status:** Remixer is independently launchable, but a new user cannot build or manage the authoritative Library from Remixer. A new installation therefore requires MIDI Library Manager first. A returning user with an already established compatible Contract-4/schema-15 Library may run the Remixer code folder without the Library Manager folder beside it, but full Library management remains unavailable until Library Manager is installed/located. See **`INSTALL_GUIDE.md`** for the complete paired and Remixer-only cases.

Install 64-bit **Python 3.11, 3.12, or 3.13** if needed. Use **Install Requirements.bat** when dependencies need installation/repair, then start with **Launch MIDI Remixer.bat** or **launch.bat**. The pinned Python packages are **PySide6 6.8.3**, **mido 1.3.3**, **rapidfuzz 3.14.3**, and **openpyxl 3.1.5**. **FluidSynth + a `.sf2`/`.sf3` SoundFont** are required for audition/audio rendering; **FFmpeg** is additionally required for MP3 output.
### First run — no authoritative Library
On a fresh profile with no established Library databases, Remixer stays disconnected. It deliberately does not start an Engine merely to create empty schema-15 databases. Choose **Open MIDI Library Manager…** to create, recover, scan, and classify the Library in MIDI Library Manager. Return to Remixer after the Library exists.
### Returning user
When an established schema-15 Library exists, Remixer attaches to the compatible Contract-4 Engine. If that Engine is stopped, Remixer may warm-start the compatible Engine because the authoritative database set already exists. Existing roots, catalogue rows, eligibility, family identity, Detailed Analysis, and prepared recommendation data are reused. A missing source drive does not cause Remixer to manufacture a new Library.
### Incompatible or offline Engine
Remixer validates Contract 4 and required capabilities. It reports an incompatible Engine and does not shut that Engine down merely to replace it. If an established Library Engine goes offline, Remixer can reconnect when the compatible Engine returns; read-only/offline behavior fails closed in the meantime.
## 3. Remix Eligibility
Library Contract 4 persists the admission state Remixer must honor. The visible **Eligibility** value is authoritative.
- **Mash Ready** — normal Guided/Assisted candidate.
- **Fusion Candidate** — alternate/version-family candidate for Version Fusion.
- **Library-usable but Not Mash Candidate** — retained, but not a normal mash candidate.
- **Needs Review** — blocked until reviewed in Library Manager.
- **Remixer-Incompatible / Unsupported** — blocked.
- **Malformed / Unusable / Rejected / Quarantined** — blocked.
Remixer rechecks persisted eligibility when a path is admitted. A raw file can be auditioned without becoming a mash source. **Open Library-Approved MIDI File into Current Mashup…** only adds a source after current Library approval is confirmed.
## 4. Main Remixer window and File actions
The File menu keeps the two products separate and explicit. Important Library-facing actions are:
- **Open MIDI Library Manager…** — launch the authoritative full Library application.
- **Select Remix Sources from Library…** — open a bounded read-only Remixer selector for choosing sources only.
- **Open Library-Approved MIDI File into Current Mashup…** — audition a chosen MIDI and admit it only if Contract-4 eligibility permits.
- **Refresh Committed Library View** — refresh Remixer’s read-only snapshot/deltas; this is not a scan.
- **Review Library Health in MIDI Library Manager…** — launch Library Manager for review/repair/quarantine work.
- **Manage Suppressed Mash / Fusion Ideas…** — manage Remixer recommendation suppression/feedback.
- **Choose Working Output Folder…** — set Remixer-owned generated output.
- **Exit** — close Remixer and reap Remixer-owned runtime resources without taking down a separately owned Library Engine.

The start/mode screen also exposes **Open Full MIDI Library…**. It launches the same complete MIDI Library Manager; it does not open a reduced duplicate browser inside Remixer.

## 5. Full Library vs Remixer selection dialogs
**MIDI Library Manager is the Library UI.** Launch it from Remixer whenever you want to browse/manage the collection itself. All Library functionality remains there, including Play/Pause, Stop, Volume, scanning, analysis, eligibility, quarantine/recovery, repair, filtering, and file actions.

Remixer uses **Select from MIDI Library…** / a smaller **Select MIDI from Library** dialog only when a remix workflow needs one or more source choices. It is intentionally not a second Library Manager. The selector is read-only, paged, and bounded for very large catalogues. It shows **Eligibility**, uses at most 100 rows per page, and avoids expensive `ResizeToContents` measurement across the catalogue-backed table.

Every Remixer MIDI selector provides exactly the transport contract needed for audition while choosing: **Play/Pause** (Resume when paused), **Stop**, and **Volume**. Double-clicking a row previews the highlighted MIDI; selection/admission is a separate explicit action. **Open Full MIDI Library…** from the selector launches the authoritative Library Manager.

For Version Fusion selection, **Version Fusion Eligible** includes both the authoritative family’s canonical **Mash Ready** member and its **Fusion Candidate** alternates. The creative Fusion logic still decides whether the selected family has a meaningful material difference.
## 6. Guided Remix
Guided is the first mode. It opens prepared relationships between distinct **Mash Ready** compositions. It is intentionally conservative about identity: a candidate is rejected from normal Mash presentation when Remixer can prove exact content, the same deep melody fingerprint, normalized same-composition identity, or a high-confidence same-artist structural near-duplicate. This protection is applied even when an older persisted Library family ID treats two filenames as separate families. Use **Open MIDI Library Manager…** when the Library needs more files or analysis; Guided itself does not add roots or run Fast Scan.

The workflow prepares ideas, lets you audition originals, explains the relationship, creates Trials/Takes, and lets you keep, customize, or reject results. **More Different Mash Ideas** remembers authoritative family IDs plus normalized family/song/melody/content identities and rechecks the prior deep profiles for the session. Once one composition has appeared, renamed exports or alternate filenames of that composition cannot consume later Guided slots. If only a few genuinely distinct ideas remain, Guided shows only those few; if none remain, it reports exhaustion and suggests Version Fusion, broader criteria, or a different starting point.
## 7. Version Fusion
Version Fusion is the second mode and is intentionally different from a normal mash. It uses the Library’s authoritative version-family identity. Contract 4 commonly stores one canonical family representative as **Mash Ready** and alternates as **Fusion Candidate**; Remixer joins those authoritative members when evaluating the family.
Useful examples include solo versus instrumental, materially different arrangements, or complementary versions. Exact copies, near duplicates, tempo-only exports, and trivial patch/instrumentation changes are suppressed when they do not add meaningful material. A same-song pair may therefore be classified as **neither mashable nor meaningfully fusable**.
## 8. Assisted Studio
Assisted uses prepared Contract-4 recommendations and gives more control over anchor choice, discovery direction, preview length, shaping, and alternatives. Normal Assisted admission is **Mash Ready**. It does not perform a 96K full-library interactive search each time; prepared relationship batches and bounded candidate reads are reused.
The setup action **Open MIDI Library Manager…** routes Library changes to Manager. Detailed-analysis information shown in Assisted is observational; policy and maintenance remain controlled by Library Manager. Recommendation cancellation stops the Remixer recommendation operation, not Library analysis.
## 9. Custom / Advanced
Custom/Advanced opens the Creative Workbench for direct arrangement decisions. Library selections still pass the central Contract-4 admission check. Custom work may expose Library-usable material according to its explicit workflow, but unresolved/rejected/quarantined/unsupported states are never silently admitted.
## 10. More Ideas, suppression, and exhausted candidate sets
Recommendation refresh is session-aware. Remixer tracks authoritative family IDs and several Remixer-side identity hints, then applies the same hard same-composition test used by the visible pair gate. This is specifically designed for large real libraries where the same song may exist under dozens of filenames. **Manage Suppressed Mash / Fusion Ideas…** restores or reviews user-suppressed relationships. When no unseen eligible ideas remain, the UI reports exhaustion rather than recycling aliases or claiming that more distinct candidates exist.
## 11. Playback and audition
Remixer owns audition/playback. The selected/highlighted item is the playback target on surfaces that use selection. Play/Pause state, Stop, and volume belong to the same playback owner. Switching pages/tabs must not leave hidden playback ownership. Exit stops Remixer-owned playback scheduler/processes/threads; it does not stop Library-owned analysis. FluidSynth and a SoundFont must be configured for playback.
## 12. Projects, sessions, Trials, Takes, and output
Project/session files and generated MIDI/audio previews are Remixer-owned. **Save Remix Project…**, **Save Session for Later**, and the Trial/Take workflows preserve creative state. **Reject Session and Clean Up…** removes Remixer-owned generated/session artifacts only; source MIDI files in the authoritative Library are not session cleanup targets.
## 13. Library navigation and separate installations
Remixer does not assume Library Manager is in the same folder. It can use a remembered launcher, the compatible Engine source location, co-located or sibling installations, `MIDI_LIBRARY_MANAGER_HOME`, or a launcher selected by the user. Once located, the launcher is remembered. Moving between the applications does not itself trigger a full Library rescan.
## 14. Shutdown and process ownership
Remixer Exit is required to leave zero **Remixer-owned** survivors: playback scheduler/audio subprocesses, operation workers, Library loader/query threads, timers, dialogs, session resources, and temporary runtime artifacts are shut down through the lifecycle coordinator. Remixer only closes/detaches its Library client/facades. It does not issue a Library Engine shutdown as part of normal Exit.
## 15. Troubleshooting
- **No committed Library:** open MIDI Library Manager and create/recover it.
- **Library Manager not found:** choose **Open MIDI Library Manager…** and locate the launcher, or set `MIDI_LIBRARY_MANAGER_HOME`.
- **Engine offline:** verify the compatible Contract-4 Library installation. Returning-user reconnect is allowed only when an established Library exists.
- **File auditions but cannot be added:** its current lifecycle/Eligibility is not approved; review it in Library Manager.
- **Needs Review / Rejected / Quarantined:** resolve the file in Library Manager. Remixer does not override that state.
- **No more mash ideas:** the distinct eligible family set may be exhausted. Try Version Fusion, broaden/change criteria, or choose another anchor.
- **No useful Fusion pair:** the available same-song versions may be too similar to contribute meaningfully.
- **No audio:** configure FluidSynth and a SoundFont.
- **Unexpected error:** consult the per-user Remixer logs/diagnostics. The release directory is not the runtime data store.
## 16. Release compatibility and upgrade expectations
This release line targets **MIDI Library Manager v0.39.3, Library Contract 4, schema 15**. Remixer does not downgrade schema 15, independently migrate the Library, or replace a newer/incompatible Engine. The release uses the matched Contract-4 shared runtime from MIDI Library Manager v0.39.3.


## 17. Runtime data and updates

Remixer runtime state, logs, working sessions, and generated output are stored outside the source repository. The authoritative Library databases remain owned by MIDI Library Manager under the shared per-user suite data area. Updating/replacing the Remixer code folder does not intentionally recreate the Library.

Keep MIDI Library Manager v0.39.3 (Contract 4 / schema 15) available when using this release. If you move Library Manager, use **Open MIDI Library Manager…** to locate it again or set `MIDI_LIBRARY_MANAGER_HOME`.

## 18. License

MIDI Remixer is provided under the **PolyForm Noncommercial License 1.0.0**; see `LICENSE.md` for the complete terms and the required notice `Copyright © 2026 Kevin Horst`. The license permits noncommercial purposes as defined by its terms; commercial use is not granted by this license. Third-party tools, SoundFonts, MIDI files, and generated/user content keep their own applicable licenses.
