# Semester Week 11 Progress Report

> **Reporting window:** 9–15 November 2026. This report describes the initial LLM integration only; Python country collectors and autonomous agent orchestration are separate future work.

## Completed Work

- Changed the project focus to **TenderScout AI**, a proposed research agent for official tenders on power-line construction, reconstruction, modernization and expansion in eight target countries.
- Implemented `src/openrouter_client.py`, a minimal OpenRouter Chat Completions API adapter with environment-variable configuration (`OPENROUTER_API_KEY`, `LLM_MODEL`) and error handling.
- Implemented `src/tender_classifier.py`, which asks the model to classify the relevance of a notice and parses/validates a strict three-field JSON response.
- Added `src/demo_openrouter.py`, a CLI for one genuine API request when a valid key and working internet are available.
- Added a clearly labeled synthetic Indonesian example, setup instructions and mocked unit tests in `tests/test_openrouter.py`.
- Added Python 3.12 execution of the project's tests to the existing GitHub Actions checks.
- After a failed initial live request with an invalid/empty completion, increased the completion token budget, disabled unnecessary reasoning for the classifier and improved provider error diagnostics. The subsequent local live run produced valid JSON.

**Live API evidence (14 November 2026, 19:23:03 UTC):** The project was executed on the user's Mac with `python -m src.demo_openrouter`. The shared terminal result reports a successful **real OpenRouter request** to `nvidia/nemotron-3.5-super-vl:free`, returning `relevant=true`, `project_type=construction`, a nonempty reason, and `source_verified=false`. Reported usage: 209 prompt tokens, 39 completion tokens, 248 total tokens, USD 0. A sanitized transcript-derived evidence record is saved as [`reports/evidence/week11_openrouter_live.json`](evidence/week11_openrouter_live.json). The evidence comes from the actual local terminal output shared by the user, not from an independent server-side reproduction or from the unit-test mocks. The input is explicitly synthetic, so the result does **not** verify an actual tender, source, contact, or deadline.

## Important Commits

- `cc677d7ee15bf2f9e567567305790d6c729a92af` — initial OpenRouter HTTP client, classification prototype, CLI and sample input. https://github.com/nastoikazhenshen/css4007-ai-agents-final-ivaikina/commit/cc677d7ee15bf2f9e567567305790d6c729a92af
- `7d92ce0200fcd43ad05796e38aa798e43bf1603a` — tests, documentation and initial report. https://github.com/nastoikazhenshen/css4007-ai-agents-final-ivaikina/commit/7d92ce0200fcd43ad05796e38aa798e43bf1603a
- `3379d2d6f193f404cb2aef3ee40d7a2e68658eb3` — improved handling and diagnostics of empty reasoning responses. https://github.com/nastoikazhenshen/css4007-ai-agents-final-ivaikina/commit/3379d2d6f193f404cb2aef3ee40d7a2e68658eb3
- The evidence and this updated report are committed later on the actual revision date; no dates are backdated.

## Current Functionality

The implementation can construct an OpenRouter Chat Completions request, select a configured model, read a provider response, and return validated provisional JSON classification for a supplied notice. It can reject invalid inputs, malformed model responses and API errors. The synthetic sample is a classroom fixture, not an official tender. The code **does not** yet autonomously select tools, verify tender status/contacts against a live official source, or write to Google Sheets.

Without a configured, valid API key and network access, the CLI exits with an explicit error rather than fabricating a classification. The **real LLM request has now succeeded in the user's local environment**, as shown by the sanitized terminal evidence. This proves the initial LLM integration, but does not yet prove that the autonomous multi-step agent works or that any real tender was verified.

## Problems Encountered

- A provider API key is sensitive and must never be committed to a public repository or embedded in example outputs.
- Free OpenRouter model identifiers, availability, rate limits and JSON compliance may change.
- The remote development environment could not reach OpenRouter directly; initial local testing also returned an invalid or empty model completion. A follow-up local run succeeded after response-budget and parsing improvements.
- Free-form LLM text can contain invalid JSON, extra fields or contradictory classifications.
- Week 11 functionality must not be confused with the multi-step A-level agent scheduled for later weeks.

## Solutions

- Used environment-variable credentials, a safe `.env.example` file and Git ignore rules; no real key is checked in.
- Made the model ID configurable rather than hard-coded to a single permanent endpoint.
- Added response format checks, a narrow JSON contract, input limits and explicit exceptions for HTTP and network failures.
- Used offline mocked HTTP/model responses for deterministic unit tests and subsequently recorded an actual local OpenRouter response as separate evidence.
- Increased the completion budget and disabled reasoning for the simple classification task; added more precise, non-secret provider diagnostics.
- Kept current verification flags false until a real original source can be checked.

## What I Learned

- LLM integration has distinct layers: request transport, model selection, response parsing, and independent validation.
- A schema-valid model classification does not establish that a tender is genuine, active, or has a verified procurement contact.
- Mock tests establish predictable application behavior but cannot substitute for evidence of an actual model response; the successful local OpenRouter request demonstrates the difference.
- Provider responses may be valid API results while lacking final text when a model exhausts its reasoning budget.
- A future A-level agent needs model-selected tools, intermediate state, adaptation, execution limits and enforceable guardrails beyond this first request.

## Next Week

- Re-run the OpenRouter classifier on additional clearly labeled public or synthetic inputs and compare its outputs with expected classifications; avoid exposing credentials.
- Implement typed raw notice records and strengthen structured output validation.
- Begin applying the classifier to stored public examples; keep explicit evidence/status checks separate.
- Prepare the tool interfaces and multi-step agent loop for Weeks 13–14.
