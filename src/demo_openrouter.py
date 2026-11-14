"""Week 11: execute one real model request on a labeled sample.

Run from repository root:
  python3 -m src.demo_openrouter
  python3 -m src.demo_openrouter --evidence reports/evidence/week11_run.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .openrouter_client import LLMError, OpenRouterClient
from .tender_classifier import analyze_notice


def main() -> int:
    parser = argparse.ArgumentParser(description="TenderScout first LLM request")
    parser.add_argument("--input", default="examples/synthetic_notice.json")
    parser.add_argument("--evidence", default=None, help="Optional sanitized JSON result file")
    args = parser.parse_args()
    try:
        notice = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = analyze_notice(notice, OpenRouterClient())
    except (OSError, ValueError, LLMError, json.JSONDecodeError) as exc:
        print(f"OpenRouter demonstration failed: {exc}", file=sys.stderr)
        return 1
    output = {
        "provider": "openrouter",
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "live_request": True,
        "input_label": "classroom_synthetic_notice",
        **result,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    if args.evidence:
        target = Path(args.evidence)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
