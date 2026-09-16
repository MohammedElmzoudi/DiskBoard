# Contributor instructions

- Treat cleanup scope as a safety boundary. Do not broaden it to increase reclaimed numbers.
- Never test destructive behavior against real user data. Use temporary disposable fixtures.
- Keep dry-run/inventory non-destructive; make failed inspections preserve candidates.
- Keep public commits free of private paths, inventories, credentials and logs. Screenshots use clearly labeled demo data.
- Area configuration is declarative. Do not add shell hooks, eval, force flags or arbitrary recursive deletion.
- Run `python3 -B -m unittest discover -v` and the PTY demo capture for CLI changes.
- Explain limitations accurately: no absolute guarantees, no invented reclaimed-space claims.
