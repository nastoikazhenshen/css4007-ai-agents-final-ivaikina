# US collectors

Available source IDs: `us_sam; plans: us_caiso_plan`.

SAM requires SAM_API_KEY: no authenticated collection completed. CAISO public Appendix H was downloaded and parsed. No claim of national US tender coverage.

Use `python -m collectors.run --source <id> --start 2026-11-16 --end 2026-11-22 --max-pages 2`. Omit page limit for the full declared scope, or add `--resume` with identical options to continue it. Add `--transport curl` only if your execution proxy requires it. Planning IDs use the separate planning command documented in `../README.md` and are delivered by the planning group.

See `source_index.json` for every audited national source, its actual access result and original evidence availability. An audited source is not automatically a working collector. Exact canonical SourceConfig files are in `../sources/`; runtime database/raw responses/document files stay outside Git. New source IDs need central provenance/status mapping before Active can be assigned.

Tests: `python -m pytest -q collectors/tests`; complete existing suite: `python -m pytest -q`. Live results are in `../reports/live/`; mock and reduced control fixtures do not substitute for live tests.
