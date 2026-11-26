# Week 13 — LLM Tool Integration

## Scope

TenderScout AI is an independent educational assistant for public power-line procurement research. It uses the existing eight-country collector registry and conservative TenderRecord validation. The Week 13 milestone demonstrates **registered, model-callable tools** and the beginning of a bounded multi-step workflow; it is not a claim of complete autonomous operation or verified current tender coverage.

## Architecture

```text
Public tender or saved CollectorRun
          |
  JSON Schema + normalization + central validation
          |
    Canonical TenderRecord (original data preserved)
          |
 OpenRouter free-model client --> native tools[] specifications
          |                        |
          |                  model tool_calls
          |                        |
          +--> BoundedOrchestrator --> Python argument/action validation
                                         |
                               read-only TenderTools handlers
                                         |
                           ToolResult + source/evidence observations
                                         |
                              back to model for another step
                                         |
                      proposed facts / sanitized execution report
                                         |
                central validator decides routing; no automatic Active
```

## Seven registered read-only tools

| Tool | Role | Permissions |
|---|---|---|
| `inspect_tender` | Inspect previously collected title, source, buyer and missing fields | Read supplied record only |
| `inspect_document` | Extract text and metadata from an approved PDF or attachment | Approved URL or mapped in-root local file only; bounded size |
| `extract_image_text` | Pass a specific authorized image to a vision-capable model | Approved image ID and allowed file root; not arbitrary filesystem |
| `translate_and_extract` | Read original-language evidence for multilingual extraction | Approved public attachment, read-only |
| `verify_status_and_deadline` | Inspect status and deadline evidence without inventing closure | Source-backed suggestion only |
| `search_official_contact` | Find procurement-relevant public contact evidence | Official allowlisted sources only |
| `assess_commercial_relevance` | Produce a provisional assessment of project relevance | No authority to change stored record or Active status |

Tool definitions: `agent/tools_impl.py`; source policy: `agent/official_research.py`; orchestration: `agent/orchestrator.py`.

## LLM selection and tool return

`BoundedOrchestrator` sends the tool definitions to the OpenRouter model. It executes **only native assistant `tool_calls`**. Each requested name and JSON argument object is checked before Python dispatch. The result is returned in a `role=tool` message using its `tool_call_id`, allowing the model to select the next action based on observations. A plain-text sentence like "call inspect_tender" is not executed as a tool.

The free-model client restricts model IDs and checks advertised tool / image support and zero token pricing against the provider catalog. Catalog advertisement is **not** proof of successful live tool selection. Actual live success requires a recorded native tool call, executed Python handler and model continuation.

## Guardrails started this week

- Read-only tool surface; no shell, publishing, bid submission, email sending or external modification.
- Approved HTTPS source domains, robots checks and request limits when public networking is explicitly enabled.
- Local document-root restrictions and limits on PDF/image processing.
- Maximum actions, documents, completions, tokens, elapsed time and reported USD cost.
- Structured results, bounded retries and safe statuses for missing keys, unsupported models and failed requests.
- Sanitized execution summary; no API keys or raw image payloads in checkpoints.
- The model cannot independently mark a tender Active, alter canonical records or turn future project plans into open tenders.

These controls are implemented in stages. Formal comprehensive guardrail evaluation is scheduled for later weeks.

## Reproducible offline demonstration

Run from the project root with Python 3.12:

```bash
python -m pip install -r collectors/requirements.txt
python -m src.demo_week13_agent
python -m pytest -q tests/test_week13_workflow.py tests/test_openrouter_agent.py tests/test_official_research.py
```

The demonstration uses an **explicit mock model** that returns three native-format tool calls: `inspect_tender`, `verify_status_and_deadline`, `search_official_contact`. This verifies Python dispatch, result handling, step limits and unchanged central routing. It does **not** count as a real LLM-selected run.

## Optional live-provider verification

Never put the credential in the repository. Provide `OPENROUTER_API_KEY` through the local environment and run:

```bash
python scripts/openrouter_benchmark.py --output var/benchmarks/week13_openrouter.json
```

Without the key or an available qualifying free model, the benchmark reports `not_run` or `blocked`; do not describe it as passed. Image and archival PDF experiments require separately supplied public files (`--mea-image` / `--pdf`) and should never be replaced with fabricated data. Review model support, API permissions and network restrictions before live use.

## Evidence vs. completion

Source collectors and the saved public notice fixtures demonstrate the ingestion boundary. Mocked tests assess the new tool dispatcher. Independent live validation of the full collector → model → tools → evidence → output flow is **not established by copying files**. Any result published as real must retain its official URL, timestamp and original evidence. This milestone does not claim automatic verification of bid deadlines, contacts or all eight markets.
