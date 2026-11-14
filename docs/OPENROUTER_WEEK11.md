# Week 11 — OpenRouter LLM integration

This is a **minimal LLM-powered prototype**, not a completed AI agent.
It classifies the relevance of a public notice to the construction or reconstruction
of electricity transmission/distribution lines (any voltage, AC/DC).
No collector, tool execution, multi-step orchestration or Google Sheets integration
is implemented in this Week 11 work.

## Prerequisites

- Python 3.12+ recommended (3.10+ for type annotations)
- An OpenRouter account and a *fresh* API key.
- A model currently available to your account; free models may change or have rate limits.

## Running locally (macOS / Linux)

From the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_openrouter.py' -v
export OPENROUTER_API_KEY='YOUR_FRESH_KEY'
export LLM_MODEL='nvidia/nemotron-3.5-super-vl:free'
python3 -m src.demo_openrouter
```

Alternatively, create a local `.env` file from `.env.example`. The Python prototype
**does not automatically load .env**; export variables in your shell, or load the local
file securely before launching. Never commit .env, credentials, or raw HTTP headers.

To save an evidence file containing the classification and model response metadata:

```bash
python3 -m src.demo_openrouter --evidence reports/evidence/week11_run.json
```

Inspect the evidence before committing it. It must contain only synthetic/public
data and no API key, personal data or confidential project information.
The JSON fixture is explicitly synthetic and **does not represent an active tender**.

If the selected free model is unavailable or doesn't follow the JSON format,
choose an available OpenRouter model and set `LLM_MODEL` accordingly.
Do not silently treat an invalid or empty response as successful.

## Files

- `src/openrouter_client.py`: HTTP POST to OpenRouter, model selection, response parsing and error handling.
- `src/tender_classifier.py`: LLM prompt and strict three-field JSON validation.
- `src/demo_openrouter.py`: one real-API request CLI when a local key is available.
- `examples/synthetic_notice.json`: safe demonstration notice.
- `tests/test_openrouter.py`: **offline mocked tests** (no external API and no real model execution).

## Reporting and Week 12+

Passing tests shows that client and validation behavior work with simulated
provider responses; it does **not** establish a successful live LLM request.
Only a real run of the command, together with sanitized output, establishes that.

Week 12 is intended for structured source records and further validation.
Week 13 introduces model-selected tools. Week 14 introduces stateful multi-step
execution and stopping conditions; do not describe the Week 11 classifier as a
fully autonomous agent.
