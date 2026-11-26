"""Offline Week 13 demonstration using MOCKED native tool-call replies.

This exercises the real Python tool dispatch and stateful orchestration. It does
NOT prove that a live LLM selected tools; use the separate OpenRouter benchmark.
"""
import json
from pathlib import Path

from agent.openrouter_client import Completion, FREE_MODELS
from agent.orchestrator import BoundedOrchestrator, AgentLimits
from integration.collector_to_tender import to_tender

ROOT = Path(__file__).resolve().parents[1]


class MockModel:
    def __init__(self):
        self.calls = 0

    def complete(self, model, messages, **kwargs):
        names = ["inspect_tender", "verify_status_and_deadline", "search_official_contact"]
        i = self.calls
        self.calls += 1
        if i < len(names):
            message = {"content": "", "tool_calls": [{
                "id": f"mock_call_{i+1}", "type": "function",
                "function": {"name": names[i], "arguments": "{}"}
            }]}
        else:
            message = {"content": json.dumps({
                "title": "Historical public-source reference notice",
                "country": "ID", "status": "unverified",
                "evidence": [], "notes": "Mocked educational demonstration"
            })}
        return Completion(model, message, "stop",
                          {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
                          0.0, 0.01, f"mock_response_{i+1}")


def main():
    path = ROOT / "tests/fixtures/collector_run_dayone_handoff.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    tender = to_tender(run, run["tenders"][0])
    report = BoundedOrchestrator(
        MockModel(), limits=AgentLimits(max_actions=3, max_completions=4)
    ).run(tender, FREE_MODELS[0], enable_network=False)
    summary = {
        "mode": "OFFLINE MOCK MODEL — not a live OpenRouter test",
        "status": report["status"],
        "tools_selected_in_mock_sequence": [x["name"] for x in report["tools"]],
        "validated_central_decision": report["validated_central_decision"],
        "canonical_record_modified": report["canonical_record_modified"],
        "total_tokens_simulated": report["usage"]["total_tokens"],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
