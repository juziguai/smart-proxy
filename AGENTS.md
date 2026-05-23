# Smart Proxy Project Rules

## Documentation Layout

- Keep `README.md` in the repository root as the single project entrypoint.
- Keep `AGENTS.md` in the repository root because coding agents and tools discover project instructions there.
- Put all other project documentation under `docs/`.
- Classify new docs by purpose instead of placing loose files in the root:
  - `docs/design/` for architecture, feature design, and implementation specs.
  - `docs/operations/` for runbooks, maintenance notes, diagnostics, and deployment notes.
  - `docs/research/` for investigations, source notes, and external references.
  - `docs/tracker/` for progress trackers and execution logs that need cross-session continuity.
- Runtime configuration files are not treated as documentation. Files such as `whitelist.txt` may stay in the root until a dedicated config directory is introduced.
- Before adding a new documentation file, choose the target `docs/` category first. If the category is unclear, prefer `docs/operations/` for operational notes and `docs/design/` for planned changes.
- `docs/` and `tests/` are local-only in this repository and must not be staged or pushed to GitHub unless the user explicitly reverses this rule.

## Privacy And Git Hygiene

- Do not stage or push runtime outputs, logs, screenshots, diagnostics, local databases, transcript-derived data, local helper scripts, or machine-specific paths.
- Treat anything containing usernames, `C:\Users\...`, `AppData`, provider health, local ports plus process details, screenshots, tokens, cookies, API keys, or session material as private unless the user explicitly says it is safe to publish.
- Before any commit, inspect `git status --short` and the staged diff. Keep `logs/`, `data/`, `diagnostics/`, `smart-proxy-stats.db*`, `docs/`, and `tests/` out of the staged set by default.

## Python Layout

- Keep `smart-proxy.py` in the repository root as the stable executable entrypoint.
- Keep reusable Python implementation under `smart_proxy/`.
- Root-level modules such as `stats_server.py` and `stats_store.py` are compatibility wrappers only. New imports should use `smart_proxy.<module>`.
- Dashboard static assets live under `web/`; `smart_proxy/stats_server.py` is responsible for serving them.
- Windows-specific proxy and process inspection helpers live in `smart_proxy/windows_network.py`.
