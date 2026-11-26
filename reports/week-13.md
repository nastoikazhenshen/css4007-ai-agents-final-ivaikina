# Semester Week 13 Progress Report

> **Reporting window:** 23–29 November 2026. **Progress recorded 27 November 2026**; this is an interim Week 13 report, not a claim that all work through 29 November had already occurred.

## Completed Work

- Added a seven-tool, read-only registry (`agent/tools_impl.py`) for inspecting collected tenders, documents and images, extracting original-language evidence, checking status/deadlines, researching public procurement contacts and assessing relevance.
- Added `agent/openrouter_client.py` with a free-model allowlist, provider catalog/capability checks and explicit API failure handling.
- Added `agent/orchestrator.py`: native OpenRouter tool-call handling, JSON argument parsing, Python dispatch, returned tool observations, limited multi-step execution and sanitized checkpoint summaries. The model cannot directly change the stored tender or promote it to Active.
- Added public-source access policy (`agent/official_research.py`) and bounded PDF/image extraction (`collectors/common/document_text.py`), including restricted local file paths and source URL checks.
- Updated the collection layer with additional official-source connectors and configurations for the eight-country scope, discovery and evidence utilities, a separate planned-project stream, candidate portfolio analysis and SQLite registry integration.
- Added offline tests for tool execution, authorization, invalid paths, official-domain restrictions, missing evidence, model/tool limits and mock multi-step runs. An explicitly mocked three-tool demonstration is in `src/demo_week13_agent.py`.
- Added an optional real OpenRouter benchmark for multilingual text, image/PDF handling and native tool round-trips. No new live-provider success is asserted merely from transferring this benchmark.
- Kept original university assignment files unchanged and excluded operational reports, credentials and internal organization-specific material from the classroom codebase.

## Important Commits

- `ab05239` — native tool registry, bounded orchestration, provider client, approved-source reader and PDF extraction.
- `24fe4f4` — updated discovery and international collector adapters.
- `e72933d` — collector/portfolio logic and unified public-source runtime.
- `97ade53` — expanded country source registry and labeled synthetic fixtures.
- `d8de93c` — tool and official-source safety tests, benchmark and offline acceptance entrypoint.
- `7005d42` — unified registry, sync orchestration and optional read-only model/country smoke scripts.
- `edff13d` — explicit mocked three-tool workflow demonstration and regression tests.
- Later revisions to this report, CI and documentation are recorded under their actual commit timestamps.

## Current Functionality

The existing Week 11 LLM classifier and Week 12 normalized tender-processing core remain available. The new code supplies **seven model-callable tools** and a multi-step loop. The orchestrator registers tool specifications, consumes native tool calls, validates arguments, executes read-only Python handlers, returns their results to the model and records action summaries. Source evidence is kept separate from model proposals. Public network access is opt-in, with allowlisted hosts and bounded document processing. Missing or expired tender information remains pending/history according to the independent validator, not the model's narrative.

**Offline reproduction:**

```bash
python -m pip install -r collectors/requirements.txt
python -m src.demo_week13_agent
python -m pytest -q tests/test_week13_workflow.py tests/test_openrouter_agent.py tests/test_official_research.py
```

`src.demo_week13_agent` simulates native-format LLM calls using a mock. It does not verify live OpenRouter capability. A separate optional benchmark using a real API key is documented in `docs/WEEK13_TOOL_INTEGRATION.md`. Results from such a run must be recorded separately and must not be inferred from mocks.

## Problems Encountered

- The public portals expose different languages, formats, access policies and sometimes image-only emails or deadlines hidden inside PDFs.
- Free model availability, tool-calling support, vision support and output formatting may change; a model merely printing a tool name is not real tool calling.
- The broader source code includes research and operational artifacts not needed for a classroom deliverable, and directly copying them risks disclosing unrelated context.
- An LLM can invent source claims or request unauthorized actions; a tool success does not establish that a procurement is currently open.
- A code import is not the same as an independently recorded successful runtime or real provider test.

## Solutions

- Kept tools read-only with explicit approved-source policy, argument checks, file-root restrictions and budgets.
- Used native `tool_calls` rather than detecting tool names in plain text, and returned observations as `role=tool` messages.
- Kept proposed facts and collected evidence separate from canonical records; only the deterministic validator decides tender routing.
- Used mock HTTP/model fixtures and synthetic records for repeatable tests; marked the example runner as mocked.
- Maintained credential-free configuration and excluded organization-specific operational documentation. Added a separate classroom architecture guide.

## What I Learned

This week clarified the difference between an LLM-powered classifier and a tool-using agent: the model must select a registered tool, Python must validate and execute that call, and the result must go back into the model context for subsequent decisions. It also showed why tool permissions, evidence provenance, finite execution budgets and explicit unknown values are necessary for a trustworthy research workflow.

## Next Week

Complete and evaluate the multi-step autonomous workflow with explicit state, conditional re-planning, failure recovery and stopping conditions. Record a genuine free-model OpenRouter tool-call round trip where available; distinguish provider restrictions from application failures. Expand at least twelve functional tests with required guardrail scenarios and prepare a qualifying recent scientific paper selection for Week 14.
