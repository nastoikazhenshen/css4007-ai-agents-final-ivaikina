"""Bridge existing Indonesia collector output to independent research collector_run JSON Schema v1.0.0.

Operates on local run data only. Does not make network requests or invent verification.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA = Path(__file__).with_name("collector_run.schema.json")
KNOWN_SOURCES = {
    "pln_batam": "https://www.plnbatam.com/pengumuman-pengadaan/",
    "world_bank": "https://search.worldbank.org/api/v2/procnotices",
    "pln_eproc": "https://eproc.pln.co.id/portal/home",
    "pln_vms": "https://vms.pln.co.id/",
    "spse_esdm": "https://spse.inaproc.id/esdm/lelang",
    "spse_jabarprov": "https://spse.inaproc.id/jabarprov/lelang",
}


def classify_error(message: str):
    s = message.lower()
    if re.search(r"\b403\b", s): return "http_403", False
    if re.search(r"\b429\b", s): return "http_429", True
    if re.search(r"\b503\b", s): return "http_503", True
    if "timeout" in s or "timed out" in s: return "timeout", True
    if "captcha" in s: return "captcha", False
    if "permission" in s or "not authorized" in s or "registration" in s or "401" in s:
        return "auth_required", False
    if "json" in s or "parse" in s or "unexpected" in s: return "parse_error", False
    return "other", False


def make_raw(record: dict, listing_url: str):
    docs = []
    for doc in record.get("documents", []):
        uri = doc.get("source_url")
        if not uri or not uri.startswith("https://"):
            continue
        docs.append({"title": doc.get("title"), "url": uri,
                     "kind": {"tender_document": "tender_notice", "tender_update": "other",
                              "specification": "specification"}.get(doc.get("document_kind"), "unknown"),
                     "access": ("public" if doc.get("download_status") == "downloaded" or doc.get("sha256") else
                                "restricted" if doc.get("download_status") == "access_restricted" else "unknown")})
    if record.get("specification_url") and not any(d["url"] == record["specification_url"] for d in docs):
        docs.append({"title": "Specification", "url": record["specification_url"],
                     "kind": "specification", "access": ("public" if doc.get("download_status") == "downloaded" or doc.get("sha256") else
                                "restricted" if doc.get("download_status") == "access_restricted" else "unknown")})
    by_ev = {e["evidence_id"]: e["source_url"] for e in record.get("evidence", [])}
    contacts = []
    for contact in record.get("contacts", []):
        ev = next((by_ev[x] for x in contact.get("evidence_ids", []) if x in by_ev), None)
        contacts.append({"name": contact.get("name"), "email": contact.get("email"),
                         "phone": None, "source_url": ev})
    published = record.get("published_at_original")
    # Preserve ambiguous or localized dates verbatim in raw_payload; never fabricate ISO date.
    published_on = published[:10] if published and re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", published) else None
    return {
        "source_tender_id": record.get("source_tender_id"), "country_code": "ID",
        "title_original": record.get("title_original"),
        "description_original": record.get("description_original"),
        "buyer_original": record.get("buyer_name_original"), "listing_url": listing_url,
        "tender_url": record.get("tender_url"), "published_on": published_on,
        "deadline_text": record.get("deadline_original"),
        "raw_status": record.get("procurement_status"), "documents": docs,
        "contact_hints": contacts, "raw_payload": record,
        "fetched_at": record["last_seen_at"]}


def make_run(src: dict, records: list[dict], started_at: str, finished_at: str):
    source_id = src["source_id"]
    url = src.get("url", KNOWN_SOURCES[source_id])
    original_status = src.get("status", "failed")
    attempts = src.get("attempts", [])
    error_messages = [f"{e.get('url', url)}: {e.get('error', e)}" if isinstance(e, dict) else str(e)
                      for e in src.get("errors", [])]
    for a in attempts:
        if a.get("error"):
            error_messages.append(f"{a['url']}: {a['error']}")
        elif a.get("http_status", 200) >= 400:
            error_messages.append(f"{a['url']}: HTTP {a['http_status']}")
    if original_status in {"permission_required", "access_restricted"}:
        error_messages.append("Permission required: " + src.get("notes", "operator approval"))
    if original_status in {"probe_only", "empty_unverified"}:
        status = "empty_unverified"
    elif original_status in {"permission_required", "access_restricted"}:
        status = "blocked"
    elif original_status == "partial":
        status = "partial"
    elif original_status == "ok":
        status = "success"
    elif any("403" in e for e in error_messages):
        status = "blocked"
    elif any("503" in e for e in error_messages):
        status = "unavailable"
    else:
        status = "failed"
    total = src.get("source_total")
    discovered = int(src.get("records_loaded", src.get("documents_discovered", len(records))))
    pages = int(src.get("pages", 0))
    verified_complete = bool(original_status == "ok" and source_id == "world_bank"
                             and total is not None and discovered >= int(total)
                             and src.get("verified_complete", False) and not error_messages)
    reason = {
        "pln_batam": "PLN Batam public index scanned, but archive completeness/pagination unproven",
        "world_bank": "World Bank response contains duplicate/overlapping pages or truncated pagination",
        "pln_vms": "Public HTML probe only; zero supplier rows not evidence of no tenders",
        "pln_eproc": "Main PLN site was unavailable; tender feed never loaded",
        "spse_esdm": "SPSE requires operator agreement for third-party crawlers",
        "spse_jabarprov": "SPSE requires operator agreement for third-party crawlers",
    }.get(source_id, "Completeness of procurement feed not confirmed")
    if verified_complete:
        reason = None
    elif status == "success":
        status = "partial"
    all_errors = [{"error_code": classify_error(m)[0], "message": str(m),
                   "retryable": classify_error(m)[1]} for m in error_messages]
    observations = [{"url": a["url"], "status_code": a.get("http_status"),
                     "observed_at": finished_at, "note": a.get("error")}
                    for a in attempts if a.get("url", "").startswith("https://")]
    raw = [make_raw(rec, url) for rec in records]
    run_id = "run_id_" + hashlib.sha256((source_id + started_at).encode()).hexdigest()[:18]
    run = {"schema_version": "1.0.0", "run_id": run_id,
           "source_id": source_id, "country_code": "ID",
           "started_at": started_at, "finished_at": finished_at, "run_status": status,
           "coverage": {"verified_complete": verified_complete, "reason": reason,
                        "date_from": src.get("date_from"), "date_to": src.get("date_to"), "next_cursor": None},
           "metrics": {"pages_requested": int(src.get("pages_requested", pages)), "pages_received": pages,
                       "records_seen": max(discovered, len(raw)), "records_emitted": len(raw),
                       "errors_count": len(all_errors)},
           "http_observations": observations, "errors": all_errors, "tenders": raw}
    return run


def validate(run: dict):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(run))
    if errors:
        raise ValueError("collector_run schema: " + "; ".join(e.message for e in errors[:6]))
    if run["metrics"]["records_emitted"] != len(run["tenders"]):
        raise ValueError("records_emitted does not equal number of tenders")
    if run["metrics"]["errors_count"] != len(run["errors"]):
        raise ValueError("errors_count does not equal errors length")
    if run["coverage"]["verified_complete"] != (run["run_status"] == "success"):
        raise ValueError("A successful run must have verified completeness")
    if run["metrics"]["pages_received"] > run["metrics"]["pages_requested"]:
        raise ValueError("Received more pages than requested")
    if run["metrics"]["records_seen"] < len(run["tenders"]):
        raise ValueError("Seen fewer records than emitted")
    if run["run_status"] == "empty_unverified" and run["tenders"]:
        raise ValueError("Empty run cannot emit tenders")
    return True


def export_collector_runs(output: Path):
    report = json.loads((output / "last_run.json").read_text(encoding="utf-8"))
    source_records = defaultdict(list)
    for name in ("active_tenders", "pending_verification", "tender_history"):
        path = output / (name + ".json")
        if path.exists():
            for obj in json.loads(path.read_text(encoding="utf-8")):
                source_records[obj["source_id"]].append(obj)
    directory = output / "collector_runs"
    directory.mkdir(exist_ok=True)
    written = []
    for src in report["sources"]:
        source_id = src["source_id"]
        run = make_run(src, source_records[source_id], report["observed_at"],
                       report.get("finished_at", report["observed_at"]))
        validate(run)
        path = directory / f"{source_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        written.append(path)
    return written


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("run_dir", nargs="?", default="evidence")
    args = p.parse_args()
    paths = export_collector_runs(Path(args.run_dir))
    for path in paths:
        obj = json.loads(path.read_text())
        print(path.name, obj["run_status"], obj["metrics"]["records_emitted"],
              "complete=", obj["coverage"]["verified_complete"])
