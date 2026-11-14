# Week 11 — OpenRouter LLM integration in PyCharm

This is a first LLM-powered classification prototype, **not** a complete autonomous
agent. Python country collectors, multi-step tool execution and Google Sheets are
not part of this Week 11 task.

## Setup on Mac (PyCharm)

1. In PyCharm, choose **Get from VCS**, paste
   `https://github.com/nastoikazhenshen/css4007-ai-agents-final-ivaikina.git`
   and open the checked-out repository. If already cloned, use **Git > Pull**.
2. In **Settings > Project > Python Interpreter**, select a Python 3.12
   environment (or create a fresh virtual environment).
3. Open the project terminal and run:
   ```bash
   python3 -m pip install -r requirements.txt
   python3 -m unittest discover -s tests -p 'test_openrouter.py' -v
   ```
4. Copy `.env.example` to `.env` in the project root. Create a **new**
   OpenRouter API key at https://openrouter.ai/settings/keys. Do not reuse a
   key that was shared in chat. Fill in the **local**, untracked `.env` file:
   ```dotenv
   OPENROUTER_API_KEY=<your newly generated private key>
   LLM_MODEL=nvidia/nemotron-3.5-super-vl:free
   ```
5. Confirm `.env` is ignored:
   ```bash
   git check-ignore .env
   ```
   It should print `.env`.
6. Run the real request in the project terminal:
   ```bash
   python3 -m src.demo_openrouter
   ```

The Python client uses `python-dotenv` and loads the project-root `.env`
automatically. Environment variables set by PyCharm Run Configuration or shell
take precedence because `override=False`. The key is never printed.

## Model

`nvidia/nemotron-3.5-super-vl:free` is the initial test choice, not a
promise of unlimited availability or guaranteed valid JSON. OpenRouter free
endpoints have rate limits, and free model availability can change. To change
models, edit **only** `LLM_MODEL` in your local `.env`; for example,
`openrouter/free` is a free-model router but can route to different models
between requests, making tests less reproducible.

## Evidence

For the Week 11 deliverable, the instructor requires a **real first LLM request**.
The committed tests are offline mocks and cannot substitute for a genuine
successful OpenRouter response.

Optionally save safe metadata from the actual run:

```bash
python3 -m src.demo_openrouter --evidence reports/evidence/week11_run.json
```

Inspect the output before uploading: no API key, secret headers, or non-public
source data. The included example is **synthetic**, not an active tender.
Do not claim a successful live call unless the command genuinely returns one.

## Files

- `src/openrouter_client.py` — OpenRouter HTTP adapter, local .env loading
- `src/tender_classifier.py` — classification prompt and JSON checks
- `src/demo_openrouter.py` — command-line entrypoint
- `examples/synthetic_notice.json` — non-real sample
- `tests/test_openrouter.py` — offline tests
- `requirements.txt` — python-dotenv dependency

Planned: Week 12 record validation; Week 13 tools; Week 14 autonomous
orchestration and stopping; do not equate a classifier with the final agent.
