"""Offline tests of the Week 12 tender processing core (no LLM or network)."""
import json
from copy import deepcopy
from pathlib import Path
import pytest

from integration.collector_validation import validate_run
from integration.collector_to_tender import convert, to_tender, require_schema
from integration.candidate_store import CandidateInbox
from integration.pipeline import process_run
from contracts.validation.validate_tender import semantic_errors

FIXTURE = Path(__file__).parent / "fixtures" / "collector_run_dayone_handoff.json"

def sample_run():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))

def test_official_fixture_is_valid():
    validate_run(sample_run())

def test_conversion_preserves_original_source():
    run = sample_run()
    record = convert(run)[0]
    original = run["tenders"][0]
    assert record["project"]["title"] == original["title_original"]
    assert record["project"]["country_code"] == original["country_code"]
    assert record["source"]["tender_url"] == original["tender_url"]
    assert record["source"]["official_url_verified"] is False

def test_canonical_schema_and_semantics():
    record = convert(sample_run())[0]
    require_schema(record, "tender.v1_1.schema.json")
    assert semantic_errors(record, None) == []

def test_historical_notice_not_marked_active():
    record = convert(sample_run())[0]
    assert record["assessment"]["decision"] != "active"
    assert record["procurement"]["status"] != "open"

def test_reject_run_with_wrong_count():
    run = sample_run()
    run["metrics"]["records_emitted"] += 1
    with pytest.raises(ValueError):
        validate_run(run)

def test_reject_insecure_source_link():
    run = sample_run()
    run["tenders"][0]["tender_url"] = "http://example.com/notice"
    with pytest.raises(ValueError):
        validate_run(run)

def test_pipeline_persists_and_is_idempotent(tmp_path):
    run = sample_run()
    inbox = CandidateInbox(tmp_path / "notices.sqlite3")
    try:
        first = process_run(run, inbox=inbox)
        assert len(first) == inbox.count() == 1
        identifier = first[0][0]["record_id"]
        assert inbox.get(identifier)["record_id"] == identifier
        before = inbox.db.total_changes
        second = process_run(run, inbox=inbox)
        assert len(second) == 1
        assert inbox.count() == 1
        assert inbox.db.total_changes == before
    finally:
        inbox.close()

def test_missing_email_not_fabricated():
    run = sample_run()
    run["tenders"][0]["contact_hints"] = []
    run["tenders"][0]["raw_payload"]["contacts"] = []
    record = to_tender(run, run["tenders"][0])
    assert not any(c.get("verified") for c in record["contacts"])
