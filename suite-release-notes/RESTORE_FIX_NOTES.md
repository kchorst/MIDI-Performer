# MIDI Performer Suite — Restore Safety Patch 1

Applies to MIDI Library Manager v0.39.3 and MIDI Remixer v0.34.10.

- Backup restore now records rollback actions before installing each staged file.
- SQLite WAL/SHM files are moved into rollback storage instead of being permanently deleted before the transaction commits.
- Reference-import directories participate in the same rollback transaction.
- Both `config/library_engine_settings.json` and `config/app_settings.json` are validated and restored when present in the backup.
- Restore staging resides in the application data directory to keep file moves on the same volume.
- A failed rollback preserves the recovery directory and reports its location instead of deleting the only remaining original data.
- Shared Library Engine manifest SHA-256 hashes were regenerated from packaged files.
- Each application includes seven self-contained regression tests in `tests/test_library_backup_regression.py`.

Run the tests from inside either application folder:

    python -m unittest discover -s tests -p 'test_library_backup_regression.py' -v

Validation completed: both sets of seven tests pass; all packaged Python sources compile; manifest hashes match.
Windows GUI execution, interactive playback, and hardware-specific audio tests were not performed in this environment.
