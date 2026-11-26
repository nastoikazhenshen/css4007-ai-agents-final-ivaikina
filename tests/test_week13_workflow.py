"""Week 13: offline tool invocation proof; no network, tokens or credentials."""
import json
from pathlib import Path

from agent.openrouter_client import FREE_MODELS
from agent.orchestrator import BoundedOrchestrator, AgentLimits
from integration.collector_to_tender import to_tender
from src.demo_week13_agent import MockModel

ROOT = Path(__file__).resolve().parents[1]


def _record():
    run = json.loads((ROOT / "tests/fixtures/collector_run_dayone_handoff.json").read_text())
    return to_tender(run, run["tenders"][0])


def test_three_tool_calls_execute_with_mocked_model():
    report = BoundedOrchestrator(
        MockModel(), limits=AgentLimits(max_actions=3, max_completions=4)
    ).run(_record(), FREE_MODELS[0], enable_network=False)
    assert report["status"] == "completed"
    assert [tool["name"] for tool in report["tools"]] == [
        "inspect_tender", "verify_status_and_deadline", "search_official_contact"
    ]
    assert all(tool["actual_model_tool_call"] for tool in report["tools"])
    assert report["canonical_record_modified"] is False
    assert report["validated_central_decision"] != "active"


def test_action_budget_stops_workflow():
    report = BoundedOrchestrator(
        MockModel(), limits=AgentLimits(max_actions=1, max_completions=4)
    ).run(_record(), FREE_MODELS[0], enable_network=False)
    assert report["status"] == "action_limit"
    assert len(report["tools"]) == 1
