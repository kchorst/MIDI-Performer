# Publishing MIDI Performer on GitHub

This guide prepares the first public repository and release for **MIDI Performer v0.7.0 Early Access**.

## Recommended repository identity

- Repository name: `MIDI-Performer`
- Description: `A desktop tool for turning rigid MIDI files into expressive, comparable performances through instrument libraries, musical interpretation, score guidance, and reference audio.`
- Visibility: Public
- Default branch: `main`
- License shown by GitHub: Other, because the repository uses PolyForm Noncommercial 1.0.0
- Suggested topics: `midi`, `music`, `fluidsynth`, `soundfont`, `pyside6`, `music-performance`, `music-interpretation`, `audiveris`

## Before publication

1. Complete the final Windows smoke test in `OmbraSmokeTest.md`.
2. Confirm that no private MIDI, PDF, MP3, project file, settings file, log, or absolute user path is present.
3. Confirm `LICENSE`, `NOTICE.md`, `README.md`, and `CHANGELOG.md` are correct.
4. Decide whether the Required Notice should remain `Copyright 2026 MIDI Performer Project` or use a named person or legal entity.
5. Do not add a Windows installer or claim one exists.

## Easiest method: GitHub Desktop

1. Extract the GitHub repository ZIP into a new folder.
2. Install and open GitHub Desktop.
3. Choose **File > Add Local Repository**.
4. Select the extracted MIDI Performer folder.
5. If GitHub Desktop says the folder is not a Git repository, choose **Create a Repository**.
6. Use repository name `MIDI-Performer` and branch `main`.
7. Review the file list before the first commit.
8. Commit with the message `Initial public release: v0.7.0 Early Access`.
9. Choose **Publish repository**.
10. Confirm the repository is public and publish it.

Official GitHub Desktop guidance: <https://docs.github.com/en/desktop/adding-and-cloning-repositories/adding-a-repository-from-your-local-computer-to-github-desktop>

## Command-line method

From the extracted repository folder:

```powershell
git init
git branch -M main
git add .
git status
git commit -m "Initial public release: v0.7.0 Early Access"
git remote add origin https://github.com/YOUR-ACCOUNT/MIDI-Performer.git
git push -u origin main
```

Replace `YOUR-ACCOUNT` with the GitHub account or organization name.

## Create the v0.7.0 release

1. Open the repository on GitHub.
2. Open **Releases**.
3. Choose **Draft a new release**.
4. Create tag `v0.7.0` from `main`.
5. Release title: `MIDI Performer v0.7.0 Early Access`.
6. Paste the text from `GITHUB_RELEASE_v0.7.0.md`.
7. Attach `MIDI-Performer-0.7.0-Early-Access.zip` if you want one named release asset in addition to GitHub's automatically generated source archives.
8. Mark it as a pre-release because it is Early Access.
9. Publish only after the final smoke test is accepted.

Official GitHub release guidance: <https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository>

## Repository settings after publication

- Enable Issues.
- Keep Discussions disabled initially unless you want to moderate a community forum.
- Add the repository description and topics.
- Set the website field later when a project page exists.
- Protect the `main` branch after collaborators begin contributing.
- Do not enable immutable releases until you are comfortable with the release workflow.

## First public issue

Create one pinned issue titled:

```text
v0.7.0 Early Access feedback and known issues
```

Use it to collect links to reproducible bugs while keeping each technical problem in its own issue.

## After publication

1. Copy the public repository URL.
2. Copy the release URL ending in `/releases/tag/v0.7.0`.
3. Capture accepted application screenshots with descriptive filenames.
4. Add the screenshots to `docs/images/` in a later documentation commit.
5. Use the repository and release links in the WordPress, LinkedIn, and Bluesky announcement.
