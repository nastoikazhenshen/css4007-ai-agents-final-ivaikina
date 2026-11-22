"""CollectorRun -> canonical TenderRecord, without pretending to run an AI agent.

Only extracted facts and deterministic interpretations are mapped. Status remains
unknown if an expired registration/submission period is the only proof of closure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, time, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker
from .commercial import initial_assessment
from .live_quality import _contact_evidence

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contracts" / "schemas"
COUNTRIES = {"ID": "Индонезия", "TH": "Таиланд", "TR": "Турция", "US": "США",
             "MX": "Мексика", "BR": "Бразилия", "AR": "Аргентина", "PE": "Перу"}
MONTHS = {"januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
          "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12}
END_STATES = {"closed", "cancelled", "awarded"}


def validator(name: str):
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def require_schema(obj: dict, name: str) -> None:
    errors = sorted(validator(name).iter_errors(obj), key=lambda e: str(e.path))
    if errors:
        raise ValueError("; ".join(f"{list(e.path)}: {e.message}" for e in errors[:8]))


def parse_date_original(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})\b", value)
    if not match:
        return None
    day, month, year = match.groups()
    m = MONTHS.get(month.lower())
    return date(int(year), m, int(day)).isoformat() if m else None


def deadline_from_raw(t: dict):
    raw = t.get("raw_payload") if isinstance(t.get("raw_payload"), dict) else {}
    tz = raw.get("deadline_timezone")
    # The bid deadline is the actual submission deadline, not registration cutoff.
    stamp = raw.get("bid_deadline_at_utc")
    if not stamp and raw.get("deadline_basis") != "registration":
        stamp = raw.get("deadline_at_utc")
    if stamp and tz:
        try:
            dt = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(ZoneInfo(tz))
            return dt.date().isoformat(), dt.strftime("%H:%M"), tz
        except (ValueError, KeyError):
            pass
    return None, None, None


def source_host(url):
    return (urlparse(url or "").hostname or "").lower()


def is_official_document(t: dict) -> bool:
    url = t.get("tender_url") or ""
    listing = t.get("listing_url") or ""
    a, b = source_host(url), source_host(listing)
    return bool(a and b and (a == b or a.endswith("." + b)) and url.startswith("https://"))



def confirmed_official_award(run: dict, notice: dict) -> dict | None:
    """Recognize a source-published award notice, never a collector's guessed status.

    World Bank procurement notice type 'Contract Award' is evidence of an
    awarded procurement. It is NOT inferred from `raw_status='closed'` or
    `processing_status='archived'`. Pin the primary official API, notice id
    in the structured locator, and original excerpt before setting awarded.
    """
    if run.get("source_id") != "world_bank":
        return None
    notice_id = notice.get("source_tender_id")
    if not isinstance(notice_id, str) or not re.fullmatch(r"OP\d{5,12}", notice_id):
        return None
    raw = notice.get("raw_payload") or {}
    expected_locator = f"$.procnotices[id={notice_id}] / status"
    for ev in raw.get("evidence") or []:
        if (ev.get("source_url") == "https://search.worldbank.org/api/v2/procnotices"
            and ev.get("locator") == expected_locator
            and ev.get("observed_at")
            and re.fullmatch(r"\s*Contract\s+Award\s*(?:\|\s*)?",
                             ev.get("original_excerpt") or "", flags=re.I)):
            return ev
    return None

def to_tender(run: dict, t: dict, *, now: datetime | None = None) -> dict:
    raw = t.get("raw_payload") if isinstance(t.get("raw_payload"), dict) else {}
    src = run["source_id"]
    retrieved = t["fetched_at"]
    direct_url = t.get("tender_url")
    deadline_date, deadline_time, deadline_tz = deadline_from_raw(t)
    published = parse_date_original(raw.get("published_at_original")) or t.get("published_on")
    text = ((t.get("title_original") or "") + " " + (t.get("description_original") or "")).lower()

    # Explicit lexical hints only, not a claim of AI semantic verification.
    work_types = []
    if re.search(r"\bpembangunan\b|\bconstruction\b", text):
        work_types.append("new_construction")
    if re.search(r"\brekonstruksi\b|\brekonstruk\b|\breconstruction\b", text):
        work_types.append("reconstruction")
    if re.search(r"\bmodernisasi\b|\bupgrade\b|\bmodernization\b", text):
        work_types.append("modernization")
    if re.search(r"\bextension\b|\bexpansion\b", text):
        work_types.append("expansion")
    line_type = "underground" if "underground" in text or "kabel bawah tanah" in text else "unknown"
    voltage = sorted({int(x) for x in re.findall(r"\b(\d{2,3})\s*k\s*v\b", text, flags=re.I)})

    evidence_raw = raw.get("evidence") or []
    mapped_evidence = []
    for ev in evidence_raw:
        loc = (ev.get("locator") or "").lower()
        if "penawaran" in loc:
            field = "procurement.deadline_date"
        elif "pendaftaran" in loc:
            field = "procurement.registration_deadline"
        elif "email" in loc:
            field = "contacts.email"
        elif "publication" in loc:
            field = "procurement.published_on"
        else:
            field = "other"
        if ev.get("source_url") and ev.get("observed_at"):
            mapped_evidence.append({
                "field_path": field,
                "source_url": ev["source_url"], "location": ev.get("locator"),
                "excerpt": ev.get("original_excerpt"), "checked_at": ev["observed_at"]})

    # Explicitly sourced line-type classification; not an LLM determination.
    if line_type == "underground" and direct_url and t.get("title_original"):
        mapped_evidence.append({
            "field_path": "project.line_type", "source_url": direct_url,
            "location": "Original tender notice title",
            "excerpt": t["title_original"], "checked_at": retrieved,
        })

    evidence_by_id = {x.get("evidence_id"): x for x in evidence_raw}
    contacts = []
    for c in raw.get("contacts") or []:
        email_in_evidence, procurement_role_verified, evidence_url, _ = _contact_evidence(c, t, src)
        validated = bool(email_in_evidence and procurement_role_verified)
        # The platform's own contact field is tender-specific; a generic PDF
        # email cannot be promoted into a procurement contact by the collector.
        kind = ("procurement_department" if validated and c.get("contact_type") == "tender_department"
                else "tender_specific" if validated and c.get("contact_type") == "tender_person"
                else "generic_corporate")
        ref = next((evidence_by_id.get(x) for x in c.get("evidence_ids", [])
                    if evidence_by_id.get(x) and evidence_by_id[x].get("source_url") == evidence_url), None)
        contacts.append({
            "name": c.get("name"), "role": c.get("department"),
            "email": c.get("email"), "phone": c.get("phone"), "official_channel_url": None,
            "kind": kind, "verified": validated,
            "evidence_url": evidence_url if email_in_evidence else None,
            "evidence_location": ref.get("locator") if ref else None})
    if not contacts:
        for c in t.get("contact_hints") or []:
            contacts.append({
                "name": c.get("name"), "role": None, "email": c.get("email"),
                "phone": c.get("phone"), "official_channel_url": None,
                "kind": "generic_corporate", "verified": False, "evidence_url": None,
                "evidence_location": None})

    documents = [{"title": x["title"], "url": x.get("url"),
                  "kind": x["kind"] if x["kind"] in {"specification","terms_of_reference","bill_of_quantities","tender_notice","other"} else "other",
                  "access": x.get("access") if x.get("access") in {"public","registration_required","unavailable","unknown"} else "unknown"}
                 for x in t.get("documents", [])]
    spec = "registration_required" if raw.get("spec_status") == "restricted" else "not_checked"
    if any(d["kind"] == "specification" and d["access"] == "public" and d["url"] for d in documents):
        spec = "found"
    # A source-provided raw/entry_status=closed is NOT official closure evidence.
    # An official Contract Award notice with source provenance is different.
    award_evidence = confirmed_official_award(run, t)
    status = "awarded" if award_evidence else "unknown"
    if award_evidence:
        mapped_evidence.append({
            "field_path": "procurement.status",
            "source_url": award_evidence["source_url"],
            "location": award_evidence["locator"],
            "excerpt": award_evidence["original_excerpt"],
            "checked_at": award_evidence["observed_at"],
        })
    cats, commercial_relevance, demand, project_flag = initial_assessment(t.get("title_original"), t.get("description_original"))
    missing = ["project.scale"]
    if status == "unknown":
        missing.insert(0, "procurement.status")
    if not any(c["verified"] and c["email"] for c in contacts):
        missing.append("contacts.email")
    if spec != "found":
        missing.append("documents.specification")
    if not deadline_date:
        missing.append("procurement.deadline_date")

    identity = (t.get('source_tender_id') or direct_url or
                f"{t.get('listing_url')}:{t.get('title_original')}")
    rid = hashlib.sha256(f"{src}:{identity}".encode()).hexdigest()[:24]
    record = {
        "schema_version": "1.1.0", "record_id": "tender_" + rid,
        "source": {"source_id": src, "source_name": src.replace("_", " ").title(),
                   "source_url": t.get("listing_url") or direct_url, "tender_url": direct_url,
                   "official_url_verified": False, "access": "public",
                   "retrieved_at": retrieved},
        "project": {"country_code": t["country_code"], "country_name_ru": COUNTRIES[t["country_code"]],
                    "buyer_name": t.get("buyer_original"), "title": t.get("title_original"),
                    "description": t.get("description_original"), "work_types": work_types,
                    "line_type": line_type, "current_type": "unknown", "voltage_kv": voltage,
                    "scale": "unknown", "scale_reason": None},
        "procurement": {"tender_number": t.get("source_tender_id"), "published_on": published,
                        "deadline_date": deadline_date, "deadline_time": deadline_time,
                        "deadline_timezone": deadline_tz, "status": status,
                        "status_checked_at": retrieved},
        "contacts": contacts, "documents": documents, "specification_status": spec,
        "assessment": {"is_power_line_project": project_flag, "decision": "pending_verification",
                       "commercial_relevance": commercial_relevance,
                       "project_categories": cats, "product_demand": demand,
                       "reason": "Initial deterministic assessment only; AI semantic verification not run.",
                       "missing_fields": missing, "confidence": 0.0},
        "evidence": mapped_evidence,
        "audit": {"last_checked_at": retrieved, "attempt_count": 0, "collection_method": "file",
                  "notes": "Historical source evidence; no fresh network verification. "
                           f"raw_status={t.get('raw_status')}; entry_status={raw.get('entry_status')}; "
                           f"document_sha256_metadata={raw.get('content_sha256') or 'unknown'}"},
    }
    record["assessment"]["decision"], record["assessment"]["reason"] = classify_record(record, now=now)
    require_schema(record, "tender.v1_1.schema.json")
    return record


def deadline_expired(record: dict, *, now: datetime | None = None) -> bool:
    p = record["procurement"]
    if not all((p["deadline_date"], p["deadline_time"], p["deadline_timezone"])):
        return False
    try:
        zone = ZoneInfo(p["deadline_timezone"])
        hh, mm = map(int, p["deadline_time"].split(":"))
        end = datetime.combine(date.fromisoformat(p["deadline_date"]), time(hh, mm), tzinfo=zone)
        return end < (now or datetime.now(timezone.utc)).astimezone(zone)
    except (ValueError, KeyError):
        return False


def has_deadline_evidence(record: dict) -> bool:
    return any(e["field_path"] == "procurement.deadline_date" and
               e.get("source_url") and e.get("excerpt") for e in record["evidence"])


def classify_record(record: dict, *, now: datetime | None = None):
    p = record["procurement"]
    a = record["assessment"]
    if a.get("commercial_relevance") == "irrelevant" or record["project"]["scale"] == "small":
        return "excluded", "Confirmed commercially irrelevant or small-scale project."
    if p["status"] in END_STATES:
        return "history", "Confirmed end-of-procurement lifecycle state."
    if deadline_expired(record, now=now) and has_deadline_evidence(record):
        return "history", "Officially published submission deadline has elapsed; lifecycle status not assumed closed."
    # Before Agent+Verification, never promote a collector's suggestion into Active.
    return "pending_verification", "Needs semantic, status and/or contact verification by central agent."


def convert(run: dict, *, now: datetime | None = None) -> list[dict]:
    from .collector_validation import validate_run
    validate_run(run)
    return [to_tender(run, t, now=now) for t in run["tenders"]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("collector_run", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run = json.loads(args.collector_run.read_text(encoding="utf-8"))
    records = convert(run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records[0] if len(records) == 1 else records,
                                      ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Converted {len(records)} record(s): " + ", ".join(x["assessment"]["decision"] for x in records))


if __name__ == "__main__":
    main()
