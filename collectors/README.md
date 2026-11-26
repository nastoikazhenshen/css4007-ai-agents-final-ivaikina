# Public Procurement Collectors — Classroom Edition

This project includes **eight geographic groups** of read-only public-source collectors, integrated with the canonical `CollectorRun` validation and tender-processing core.

The Indonesian collector uses its own mature runner. The other seven countries use a shared international runtime. Source connectors are not a guarantee of comprehensive coverage or confirmed active opportunities. Some require API credentials, while planning-project sources are exploratory and **must not** be treated as currently open tenders.

## Installation

From the repository root, with Python 3.12:

```bash
python -m pip install -r collectors/requirements.txt
```

## International runner (seven countries)

```bash
python -m collectors.run --help
python -m collectors.run --source br_pncp --start 2026-11-16 --end 2026-11-22 --max-pages 2
python -m collectors.run --source tr_world_bank --page-size 100
```

Available country groups: AR, BR, MX, PE, TH, TR and US. Select `--source` from `collectors/sources/*.json`. Some sources require environment variables, e.g. `SAM_API_KEY` for `us_sam`. Only use approved official public endpoints; never bypass registration, rate limits or access controls.

## Indonesia runner

```bash
python collectors/indonesia/indonesia.py --help
```

This collector has its own modules in `collectors/indonesia/` and additional browser support. A browser run needs Playwright and an installed Chromium runtime. The CLI help lists its actual output and run modes.

## Planned infrastructure projects

```bash
python -m collectors.planning.run --help
```

These records are **leads for future projects**, not published open procurements. Keep them separate from verified tenders.

## Offline tests

```bash
python -m pytest -q collectors/tests tests/test_tender_core.py
```

The tests use deterministic fixtures. Real network results vary by country, service availability, API permissions and date. The original Week 11 classifier (`src/tender_classifier.py`) remains independent. Week 13 adds a separate OpenRouter model-tool interface (`agent/orchestrator.py`) and bounded read-only tools (`agent/tools_impl.py`). The model now has a tool-call execution path, while automatic verification of every live source is not yet demonstrated. See [Week 13 tool integration](../docs/WEEK13_TOOL_INTEGRATION.md).

## Privacy, credentials and source verification

This classroom project is independent of any employer or organization. All operational API keys must stay in local environment variables, never in Git. Missing contacts or unknown deadlines must remain unverified. Source outputs do not become `Active` until the canonical validator has sufficient independent evidence.
