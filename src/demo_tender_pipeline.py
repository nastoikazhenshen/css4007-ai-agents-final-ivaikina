"""Week 12 deterministic public-notice pipeline demo. No LLM key needed."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from integration.pipeline import process_run
from integration.candidate_store import CandidateInbox

ROOT = Path(__file__).resolve().parents[1]
def main():
    example = ROOT / "tests/fixtures/collector_run_dayone_handoff.json"
    run = json.loads(example.read_text(encoding="utf-8"))
    with TemporaryDirectory() as directory:
        inbox = CandidateInbox(Path(directory) / "demo.sqlite3")
        try:
            processed = process_run(run, inbox=inbox)
            for record, operation in processed:
                print(json.dumps({
                    "country": record["project"]["country_code"],
                    "title": record["project"]["title"],
                    "tender_url": record["source"]["tender_url"],
                    "decision": record["assessment"]["decision"],
                    "deadline": record["procurement"]["deadline_date"],
                    "verified_contacts": [x["email"] for x in record["contacts"] if x.get("verified") and x.get("email")],
                    "sheets_preview": operation["tab"] if operation else None,
                    "stored": inbox.get(record["record_id"]) is not None,
                }, indent=2, ensure_ascii=False))
        finally:
            inbox.close()
if __name__ == "__main__":
    main()
