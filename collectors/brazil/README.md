# BR collectors

Available source IDs: `br_pncp, br_compras, br_world_bank; plans: br_wb_projects`.

PNCP publication live run is page-bounded; Compras week/modality 6 completed. APIs overlap by PNCP identifier. ONS summary parser is offline/disabled.

Use `python -m collectors.run --source <id> --start 2026-11-16 --end 2026-11-22 --max-pages 2`. Omit page limit for the full declared scope, or add `--resume` with identical options to continue it. Add `--transport curl` only if your execution proxy requires it. Planning IDs use the separate planning command documented in `../README.md` and are delivered by the planning group.

See `source_index.json` for every audited national source, its actual access result and original evidence availability. An audited source is not automatically a working collector. Exact canonical SourceConfig files are in `../sources/`; runtime database/raw responses/document files stay outside Git. New source IDs need central provenance/status mapping before Active can be assigned.

Tests: `python -m pytest -q collectors/tests`; complete existing suite: `python -m pytest -q`. Live results are in `../reports/live/`; mock and reduced control fixtures do not substitute for live tests.
