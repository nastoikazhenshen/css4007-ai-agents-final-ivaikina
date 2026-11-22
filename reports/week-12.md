# Semester Week 12 Progress Report

> **Reporting window:** 16–22 November 2026. The Week 12 deliverable is a working, deterministic tender-processing core. Autonomous LLM tool selection and live country collection are not claimed as completed.

## Completed Work

- Preserved the existing Week 11 OpenRouter client and LLM classification prototype.
- Added modular public-procurement processing: collector-run validation, source normalization, deadline/status assessment, evidence and contact checks, canonical structured TenderRecord creation, SQLite candidate storage and a side-effect-free Google Sheets row preview.
- Added versioned JSON Schemas for collector runs, source metadata, tender records, batch records, actions and spreadsheet rows.
- Imported a public-source reference notice for repeatable offline tests, without requiring network access or API credentials.
- Added `src/demo_tender_pipeline.py` and `tests/test_tender_core.py` for a local core-functionality demonstration.
- Extended CI to install `jsonschema` and `pytest` and invoke the new tests.
- Kept the instructor's README and level requirements unchanged.

## Important Commits

- `77203fd` — normalization, validation, quality checks and SQLite inbox.
- `45af5e7` — lifecycle validation, agent data contract and row projection.
- `b36242d` — versioned JSON Schema contracts.
- `0d91771` — saved public-source reference fixtures.
- `e3b2c12` — Week 12 demo and focused offline tests.
- `bdb5152` — correct dependency formatting.
- `cda00c9` — run new tests in GitHub Actions.

## Current Functionality

The core accepts a previously collected, schema-valid public notice batch, rejects malformed input, maps each notice into a structured canonical record, applies conservative status/deadline/evidence rules, saves the original and normalized information in SQLite by stable record ID and constructs a validated preview for downstream reporting. It preserves the distinction between an unverified candidate and a proven active tender. The Week 11 LLM classifier is still available as a separate executable; it is **not yet an autonomous controller of the processing pipeline**.

To demonstrate without network or secrets:

```bash
python -m pip install -r requirements.txt
python -m src.demo_tender_pipeline
python -m pytest -q tests/test_tender_core.py
python -m unittest discover -s tests -p 'test_openrouter.py' -v
```

The fixture represents a saved source notice, not proof that the opportunity remains open today. The Google Sheets output is a validated preview rather than an external write. These commands are the verification procedure; CI completion should be checked in GitHub Actions before claiming passing results.

## Problems Encountered

- Real portals return incomplete or historical records and often put deadlines and contacts in linked documents.
- An unverified source status, missing submission time or generic email must not be promoted to a verified active opportunity.
- The source project contains operational infrastructure unnecessary for a Week 12 classroom milestone; directly copying all components would obscure the core and require extra credentials or access.

## Solutions

- Introduced schema and business-rule validation before storage/report projection.
- Preserved original text, source URLs, evidence fields and unknown values.
- Used deterministic reference fixtures and SQLite so the core can be demonstrated without a live collector or account credentials.
- Deliberately separated Week 12 core processing from future LLM-directed tool execution and live source collection.

## What I Learned

I learned to distinguish source extraction from verification, enforce structured contracts independently of LLM output, retain provenance, and use idempotent storage to prevent duplicate records. A passing unit test with saved input does not establish live portal coverage or autonomous reasoning.

## Next Week

Implement at least three meaningful, permission-limited tools; register them for LLM-selected use; connect classification and verification actions through an explicit tool execution interface; start testing multi-step decisions and logging for the A-level target.
