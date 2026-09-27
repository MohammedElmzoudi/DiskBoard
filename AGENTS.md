# Contributor instructions

- Treat cleanup scope as a safety boundary. Do not broaden it to increase reclaimed numbers.
- Never test destructive behavior against real user data. Use temporary disposable fixtures.
- Keep dry-run/inventory non-destructive; make failed inspections preserve candidates.
- Keep public commits free of private paths, inventories, credentials and logs. Screenshots use clearly labeled demo data.
- Area configuration is declarative. Do not add shell hooks, eval, force flags or arbitrary recursive deletion.
- Run `python3 -B verify_release.py` for CLI changes. It runs `python3 -B -m unittest discover -v` and the PTY capture with a disposable HOME and temporary directory. Never run cleanup tests with the ordinary home directory.
- Explain limitations accurately: no absolute guarantees, no invented reclaimed-space claims.
