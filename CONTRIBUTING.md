# Contributing

MIDI Performer 0.7.0 is in Early Access. Reproducible bug reports and focused fixes are welcome.

## Before opening an issue

1. Read `docs/Troubleshooting.md`.
2. Reproduce the problem from a clean launch when possible.
3. Record exact steps, expected behavior, actual behavior, and environment details.
4. Attach only files you are permitted to share.
5. Prefer a small synthetic or public-domain MIDI for public reports.

## Development principles

- Preserve Original as an immutable baseline.
- Keep the user in control of musical decisions.
- Treat instrument-library choice, instrumentation, register, and balance as part of the performance.
- Keep Track Mixer ownership of track sound decisions.
- Keep playback, interpretation, comparison, and export controls in their established workflow areas.
- Describe current behavior honestly.
- Do not add marketing claims that are not verified.
- Do not commit logs, caches, local configuration, generated Takes, exports, downloaded third-party binaries, downloaded SoundFonts, or copyrighted test material.
- Do not expose command windows during normal `.pyw` use.

## Before submitting code

```powershell
.\.venv\Scripts\python.exe -m compileall -q app core tests main.py
.\.venv\Scripts\python.exe -m pytest -q
```

Update user documentation when behavior, terminology, setup, or limitations change.

## License

By contributing, you agree that your contribution may be distributed under the repository license.
