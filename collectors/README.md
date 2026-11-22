# Seven-country collector pilot

Python 3.12 read-only collectors for Brazil, USA, Turkey, Thailand, Mexico, Peru and Argentina. This extends the merged Indonesia module: its `State` primitives and the existing canonical `CollectorRun`, `SourceConfig`, central adapter, business validator, SQLite inbox and SheetsRow **projection** are reused. No network Sheets integration, LLM, orchestrator, PostgreSQL or UI is added.

## Install and reproduce

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r collectors/requirements.txt
python -m pytest -q
python -m collectors.run --source br_pncp --start 2026-11-16 --end 2026-11-22 --modalities 6 --max-pages 2 --document-limit 2
python -m collectors.run --source br_compras --start 2026-11-16 --end 2026-11-22 --modalities 6 --page-size 500
python -m collectors.run --source pe_oece --start 2026-11-01 --end 2026-11-22 --oece-pagination cursor --page-size 50 --max-pages 2
python -m collectors.run --source ar_energia_tenders --start 2026-11-16 --end 2026-11-22 --document-limit 2
python -m collectors.run --source tr_world_bank --page-size 1000
```

Dates are explicit source-query bounds, not a claim that every collected notice is current. World Bank notices are **all available history**, filtered by borrower country, not by these CLI dates. OECE uses monthly segmentation; `tenderTitle` searches procedure nomenclature, not a reliable general description keyword. Prefer unfiltered monthly extraction then local screening.

`--transport curl` is optional for an execution environment with a curl-supported network proxy. Default deployment uses httpx. Both use the same bounded retries, exact official-host allowlist, no authorization/CAPTCHA bypass and raw response storage. URLs in logs/checkpoints redact credentials. Production SAM key is read from `SAM_API_KEY`, never committed. SAM has mock coverage; no authenticated data run has been completed.

## Full, bounded and incremental collection

Omit `--max-pages` to attempt the entire declared scope. This does not mean all national tenders: PNCP/Compras also require modalities and a date window; OECE requires month/source partitions; ministry HTML is an explicitly seeded project page. Iterate the required windows/modalities for bootstrap. A page budget produces `partial`, not `success`; use **the same options plus `--resume`** to continue the saved cursor. Keep page size, scope and output directory unchanged. Inconsistent totals, missing IDs, repeated pages/records or unsafe next URLs stop the scope and preserve raw data/checkpoints. Source totals can change during a scan; restart that scope rather than claim completeness.

PNCP `--mode incremental` uses its documented `atualizacao` endpoint (live-tested). `--open-proposals --end 2026-12-22` uses the separately documented `proposta` endpoint (live-tested); this still does not promote a card to Active. Compras incremental mode safely refreshes the declared publication window; it does **not** pretend unsupported update-range parameters exist. World Bank, ministry HTML and project registries use complete scoped refreshes and idempotent upserts. For existing records outside a rolling window, periodically replay a wider publication range or PNCP updates. A verified source watermark is never advanced after partial coverage.

World Bank notices explicitly sort by `id` ascending. The default API order produced overlapping pages; the adapter detects this. Adjacent sorted pages were probed before the full sorted run. Country is checked on every normalized row. All categories and raw rows are persisted **before** screening. Broad multilingual relevance finds OHL/distribution, insulators and fittings without voltage/quantity/email gates; it is candidate screening, not final commercial qualification. Raw nonmatches remain available for reprocessing. Archived notices are research/test candidates and must not be described as open opportunities.

## Storage, documents, provenance

Runtime output defaults to `var/collectors/` and must not be committed. `state/indonesia.sqlite` is the inherited State filename in a **separate directory**, not the operating Indonesian database. It contains original gzip responses (`raw`), all unfiltered page rows (`page_rows`), scoped checkpoints, canonical collector candidates, history, document metadata and extracted PDF text. The central inbox is separate, `central_inbox.sqlite`. Failed scans never delete previous candidates. Downloaded files are content-addressed by SHA-256; download URL, status, reason and local path remain attached to the source card. Listing-only updates preserve prior downloaded evidence. Explicit removal from a fresh document list is retained as a change.

File downloads additionally check robots and approved HTTPS hosts. Login/HTML/error pages are not saved as PDFs. Public documents may be fetched; restricted/unconfirmed access retains URL and reason. A separate document budget bounds enrichment without dropping cards. PDF email occurrences retain context in `raw_payload.document_contact_candidates`; they are **not** automatically buyer contacts. Structured API contact fields retain source URL and evidence. Missing contact/deadline never removes a candidate.

PNCP and Compras can describe the same purchase: exact PNCP IDs/`raw_payload.canonical_identity` identify this overlap. Source-level SQLite replay creates no duplicate rows. Counts across sources are **not a count of unique commercial opportunities**; central cross-source identity consolidation is an architecture task.

## Verification

Unit/mock tests are separate from live capture reports. They cover retries/statuses/timeouts, malformed JSON, pagination/full/bounded/resume, empty-unverified responses, official URL constraints, missing-email retention, canonical schemas, central integration, SQLite integrity, duplicate replay/history and PDF persistence/restrictions. To replay saved real runs without making a fresh network claim:

```bash
python -m collectors.verify_outputs var/collectors/br_pncp/collector_run.json --report replay.json
```

The report tables and synthetic control JSON distinguish full scoped scans, explicit budgets, errors, missing keys and unverified permissions. Original audit captures were checked locally; only `collectors/fixtures/` contains bundled test fixtures. No cookies, browser sessions, API keys, temporary databases or bulk document binaries are in Git.

## Future projects

The separate planning group uses `python -m collectors.planning.run --source us_caiso_plan`, `tr_sbb_plan`, `ar_energia_plan`, or `<country>_wb_projects`. It stores `planned_project_proposal` draft objects in a separate table/kind and JSON files. These are **not** CollectorRun tenders, are never fed into central tender validation, and are not open tenders. See `planning/INTERFACE_PROPOSAL.md` for the proposed architecture interface. EGAT, CENACE and ONS parsers have inspected official extracts but autonomous collection is disabled until scoped permission/access review is resolved.

## Remaining architecture integration gaps

See `reports/ARCHITECTURE_HANDOFF.md`. New source IDs are not yet in central contact-provenance/official-host recognition; multilingual status/deadline handling and exact cross-source PNCP deduplication need central approval. No central contracts or Indonesia/agent files have been changed. Zero Active is an honest outcome until the central system verifies deadline, status, official URL and contact evidence.

GitHub privacy: actual candidate rows, contact values, source prose, sessionized/cursor URLs and document correspondence are excluded. Fixtures are synthetic and explicitly not live evidence. Actual captured records remain in ignored runtime storage; aggregate live/replay results are published.
