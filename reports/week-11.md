# Semester Week 11 Progress Report

> **Reporting window:** 9–15 November 2026. This report describes the initial LLM integration only; Python country collectors and autonomous agent orchestration are separate future work.

## Completed Work

- Changed the project focus to **TenderScout AI**, a proposed research agent for official tenders on power-line construction, reconstruction, modernization and expansion in eight target countries.
- Implemented `src/openrouter_client.py`, a minimal OpenRouter Chat Completions API adapter with environment-variable configuration (`OPENROUTER_API_KEY`, `LLM_MODEL`) and error handling.
- Implemented `src/tender_classifier.py`, which asks the model to classify the relevance of a notice and parses/validates a strict three-field JSON response.
- Added `src/demo_openrouter.py`, a CLI for one genuine API request when a valid key and working internet are available.
- Added a clearly labeled synthetic Indonesian example, setup instructions and mocked unit tests in `tests/test_openrouter.py`.
- Added Python 3.12 execution of the project's tests to the existing GitHub Actions checks.

**Evidence boundary:** The code and mocked tests are available in this repository. A successful live OpenRouter request has **not been verified from the development environment**: direct network requests to OpenRouter were unavailable. The first genuine model response still needs to be run locally and documented. Mocked responses must not be presented as actual LLM outputs.

## Important Commits

- `cc677d7ee15bf2f9e567567305790d6c729a92af` — initial OpenRouter HTTP client, classification prototype, CLI and sample input. https://github.com/nastoikazhenshen/css4007-ai-agents-final-ivaikina/commit/cc677d7ee15bf2f9e567567305790d6c729a92af
- Subsequent Week 11 commit(s) add tests, documentation, environment example and this report. See dated GitHub history for the precise hashes; no earlier dates are claimed.

## Current Functionality

The implementation can construct an OpenRouter Chat Completions request, select a configured model, read a provider response, and return validated provisional JSON classification for a supplied notice. It can reject invalid inputs, malformed model responses and API errors. The synthetic sample is a classroom fixture, not an official tender. The code **does not** yet autonomously select tools, verify tender status/contacts against a live official source, or write to Google Sheets.

Without a configured, valid API key and network access, the CLI exits with an explicit error rather than fabricating a classification. The integration is therefore **implemented and testable with mocks, but the real LLM request remains unverified** until a local live run.

## Problems Encountered

- A provider API key is sensitive and must never be committed to a public repository or embedded in example outputs.
- Free OpenRouter model identifiers, availability, rate limits and JSON compliance may change.
- The development environment could not reach OpenRouter directly, preventing a reliable live-response claim.
- Free-form LLM text can contain invalid JSON, extra fields or contradictory classifications.
- Week 11 functionality must not be confused with the multi-step A-level agent scheduled for later weeks.

## Solutions

- Used environment-variable credentials, a safe `.env.example` file and Git ignore rules; no real key is checked in.
- Made the model ID configurable rather than hard-coded to a single permanent endpoint.
- Added response format checks, a narrow JSON contract, input limits and explicit exceptions for HTTP and network failures.
- Used offline mocked HTTP/model responses for deterministic unit tests, with separate instructions for real local execution.
- Kept current verification flags false until a real original source can be checked.

## What I Learned

- LLM integration has distinct layers: request transport, model selection, response parsing, and independent validation.
- A schema-valid model classification does not establish that a tender is genuine, active, or has a verified procurement contact.
- Mock tests establish predictable application behavior but cannot substitute for evidence of an actual model response.
- A future A-level agent needs model-selected tools, intermediate state, adaptation, execution limits and enforceable guardrails beyond this first request.

## Next Week

- Run the real OpenRouter example on a local machine with a newly issued key; record sanitized output and exact model ID, or document any verified error without exposing credentials.
- Implement typed raw notice records and strengthen structured output validation.
- Begin applying the classifier to stored public examples; keep explicit evidence/status checks separate.
- Prepare the tool interfaces and multi-step agent loop for Weeks 13–14.
