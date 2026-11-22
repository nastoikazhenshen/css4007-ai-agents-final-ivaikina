"""Read-only, conservative quality audit of Indonesia CollectorRun -> TenderRecord.

This report does not modify source collectors, promote Active, or make network calls.
`verified_procurement_email` means the *published address and procurement contact
role* are supported by a matching official tender-specific evidence reference.
It does NOT establish mailbox deliverability, ownership or a current employee.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

KNOWN_SOURCE_HOSTS = {
    "pln_batam": ("plnbatam.com",),
    "world_bank": ("worldbank.org",),
    "pln_eproc": ("pln.co.id",),
    "pln_vms": ("pln.co.id",),
    "spse_esdm": ("inaproc.id",),
    "spse_jabarprov": ("inaproc.id",),
}

PROCUREMENT_WORDS = re.compile(r"pengadaan|pendaftaran|pelelangan|procurement|tender\s+registration", re.I)
GENERIC_CONTACT_NAMES = re.compile(
    r"\b(department|division|officer|office|procurement|purchasing|administration|team|unit|"
    r"departmental|pengadaan|contact|secretariat|committee|panitia|pelaksana|strategic)\b", re.I
)


def is_official_url(url: str | None, source_id: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower().rstrip(".")
    if u.scheme != "https" or not host or u.username or u.password:
        return False
    return any(host == allowed or host.endswith("." + allowed)
               for allowed in KNOWN_SOURCE_HOSTS.get(source_id, ()))


def is_direct_official_url(notice: dict, source_id: str) -> bool:
    url = notice.get("tender_url")
    if not is_official_url(url, source_id) or url == notice.get("listing_url"):
        return False
    u = urlsplit(url)
    if source_id == "world_bank":
        return bool(re.fullmatch(r"/en/projects-operations/procurement-detail/OP\d+/?", u.path))
    if source_id == "pln_batam":
        return u.path.lower().endswith(".pdf") and "/wp-content/uploads/" in u.path
    if source_id.startswith("spse_"):
        return bool(re.search(r"/lelang/\d+", u.path))
    return bool(u.path and u.path.rstrip("/") not in {"", "/", "/portal/home"})


def _contact_evidence(contact: dict, notice: dict, source_id: str) -> tuple[bool, bool, str | None, str]:
    """Return (email evidenced, procurement-role evidenced, evidence URL, reason).

    Accept only explicit source-specific official evidence. A lexical occurrence
    of an address somewhere in a PDF is *not* proof of its procurement role.
    """
    email = (contact.get("email") or "").strip().lower()
    raw = notice.get("raw_payload") or {}
    refs = set(contact.get("evidence_ids") or [])
    if not email or not refs:
        return False, False, None, "no_linked_evidence"
    for ev in raw.get("evidence") or []:
        if ev.get("evidence_id") not in refs:
            continue
        url = ev.get("source_url")
        excerpt = ev.get("original_excerpt") or ""
        locator = ev.get("locator") or ""
        if not is_official_url(url, source_id):
            continue
        if email not in excerpt.lower():
            continue
        # API locator must explicitly identify THIS notice's contact field.
        if source_id == "world_bank":
            anchored = locator == f"$.procnotices[id={notice.get('source_tender_id')}].contact_*"
            official_endpoint = (urlsplit(url).hostname or "").lower() == "search.worldbank.org" and urlsplit(url).path == "/api/v2/procnotices"
            role = (anchored and official_endpoint and contact.get("contact_type") in
                    {"tender_person", "tender_department"})
            return True, bool(role), url, "official_api_contact_field" if role else "no_tender_specific_contact_field"
        if source_id == "pln_batam":
            linked = url == notice.get("tender_url") and is_direct_official_url(notice, source_id)
            if not linked:
                continue
            # Explicit procurement/registration language IN THE SOURCE EXCERPT
            # (in addition to source contact type and procurement locator).
            role = bool(contact.get("contact_type") == "tender_department" and
                        "procurement email" in locator.lower() and PROCUREMENT_WORDS.search(excerpt))
            return True, role, url, "official_notice_procurement_context" if role else "email_without_procurement_role_proof"
        # Other sources have no approved contact-role provenance contract yet.
        return True, False, url, "no_contact_role_contract_for_source"
    return False, False, None, "no_matching_official_evidence"


def _person_name(name: str | None) -> bool:
    name = (name or "").strip()
    return bool(name and not GENERIC_CONTACT_NAMES.search(name) and
                len(name.split()) >= 2 and any(c.isalpha() for c in name))


def _documents(notice: dict, dest: Path | None) -> tuple[Counter, list[str]]:
    out = Counter()
    issues = []
    raw = notice.get("raw_payload") or {}
    docs = raw.get("documents") or []
    # Fallback outer documents only if original raw metadata is absent.
    if not docs:
        docs = [{"source_url": d.get("url"), "document_kind": d.get("kind"),
                 "download_status": "unknown"} for d in (notice.get("documents") or [])]
    out["documents"] = len(docs)
    for doc in docs:
        url = doc.get("source_url") or doc.get("url")
        kind = (doc.get("document_kind") or doc.get("kind") or "").lower()
        status = (doc.get("download_status") or "").lower()
        if url and urlsplit(url).path.lower().endswith(".pdf"):
            out["pdf_references"] += 1
        if kind == "specification" or (url and "specification" in url.lower()):
            out["specification_references"] += 1
        if status == "downloaded":
            out["reported_downloaded"] += 1
            local = doc.get("local_path")
            if not local:
                out["saved_files_unverifiable"] += 1
                continue
            p = Path(local)
            # Only accept paths inside the bounded run output, never arbitrary paths.
            if dest is None or not p.is_absolute() or not p.resolve().is_relative_to(dest.resolve()):
                out["saved_files_unverifiable"] += 1
                continue
            if not p.is_file():
                out["downloaded_but_local_file_missing"] += 1
                issues.append("downloaded_document_not_on_disk")
                continue
            expected = doc.get("sha256")
            if not expected or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                out["saved_files_hash_mismatch"] += 1
                issues.append("saved_document_sha256_missing_or_mismatch")
                continue
            out["saved_files_sha256_verified"] += 1
        elif status in {"access_restricted", "failed", "error", "not_downloaded"} or doc.get("download_reason"):
            out["download_issues"] += 1
            if status == "access_restricted":
                out["access_restricted_documents"] += 1
    if raw.get("specification_url") and not out["specification_references"]:
        out["specification_references"] += 1
    return out, issues


def audit_candidate(run: dict, notice: dict, tender: dict, inbox, *, file_root: Path | None) -> dict:
    """Validate a candidate's important fields and raw losslessness after DB upsert."""
    src = run["source_id"]
    rid = tender["record_id"]
    t_title = notice.get("title_original")
    t_url = notice.get("tender_url")
    title_ok = bool(isinstance(t_title, str) and t_title.strip())
    title_preserved = tender["project"]["title"] == t_title
    country_preserved = tender["project"]["country_code"] == notice["country_code"] == run["country_code"]
    direct = is_direct_official_url(notice, src)
    official = is_official_url(t_url, src)
    country_ok = bool(notice["country_code"] == "ID")

    # A record without contact is preserved, not silently excluded.
    raw = notice.get("raw_payload") or {}
    raw_contacts = raw.get("contacts") or []
    hints = notice.get("contact_hints") or []
    emails = {str(c.get("email")).strip().lower() for c in raw_contacts + hints if c.get("email")}
    names = [str(c.get("name")).strip() for c in raw_contacts + hints if c.get("name")]
    phones = {str(c.get("phone")).strip() for c in raw_contacts + hints if c.get("phone")}
    final_emails = {str(c.get("email")).strip().lower() for c in tender["contacts"] if c.get("email")}
    missing_contact_emails = emails - final_emails
    classifications = []
    for c in raw_contacts:
        if not c.get("email"):
            continue
        supported, procurement, evurl, reason = _contact_evidence(c, notice, src)
        classifications.append({
            "email": c["email"], "evidence_url": evurl,
            "email_in_official_evidence": supported,
            "verified_procurement_email": supported and procurement,
            "reason": reason,
        })
    # Contact hints without linked raw evidence must not be counted as verified.
    for h in hints:
        if h.get("email") and not any(c["email"].lower() == h["email"].lower() for c in classifications):
            classifications.append({"email": h["email"], "evidence_url": None,
                                    "email_in_official_evidence": False,
                                    "verified_procurement_email": False,
                                    "reason": "hint_without_provenance"})
    verified_emails = {x["email"].lower() for x in classifications if x["verified_procurement_email"]}
    sourced_emails = {x["email"].lower() for x in classifications if x["email_in_official_evidence"]}

    row = inbox.db.execute("SELECT raw_json, tender_json FROM candidate_inbox WHERE record_id=?", (rid,)).fetchone()
    sqlite_raw_preserved = bool(row and json.loads(row[0]) == notice)
    sqlite_tender_preserved = bool(row and json.loads(row[1]) == tender)
    doc_metrics, doc_issues = _documents(notice, file_root)
    normalized_document_urls = {d["url"] for d in tender["documents"] if d.get("url")}
    original_document_urls = {d.get("url") for d in notice.get("documents") or [] if d.get("url")}
    document_links_preserved = original_document_urls <= normalized_document_urls
    fields_preserved = (title_preserved and country_preserved and
                        tender["source"]["tender_url"] == t_url and
                        tender["project"]["buyer_name"] == notice.get("buyer_original") and
                        document_links_preserved and not missing_contact_emails)
    warnings = []
    if not title_ok: warnings.append("title_missing")
    if not t_url: warnings.append("direct_tender_url_missing")
    elif not direct: warnings.append("official_direct_url_not_structurally_confirmed")
    if not country_ok: warnings.append("unexpected_country")
    if emails and not verified_emails: warnings.append("email_procurement_role_not_verified")
    if not emails: warnings.append("no_email")
    if not fields_preserved: warnings.append("normalized_fields_not_preserved")
    if not sqlite_raw_preserved or not sqlite_tender_preserved: warnings.append("sqlite_fields_not_preserved")
    warnings.extend(doc_issues)
    if not emails and not names and not phones and tender["assessment"]["decision"] not in {"history", "excluded", "pending_verification"}:
        warnings.append("contactless_promoted_active")

    return {
        "record_id": rid, "source_tender_id": notice.get("source_tender_id"),
        "country_code": notice["country_code"], "title": t_title,
        "tender_url": t_url, "route": tender["assessment"]["decision"],
        "title_present": title_ok, "title_preserved": title_preserved,
        "country_valid": country_ok, "country_preserved": country_preserved,
        "tender_url_present": bool(t_url), "tender_url_official_host": official,
        "official_direct_url_structural": direct,
        "has_email": bool(emails), "has_contact_name": bool(names),
        "has_named_person": any(_person_name(n) for n in names),
        "has_phone": bool(phones), "no_contacts": not(emails or names or phones),
        "has_verified_procurement_email": bool(verified_emails),
        "has_unverified_email": bool(emails - verified_emails),
        "has_email_with_official_evidence": bool(sourced_emails),
        "contact_evidence": classifications,
        "sqlite_raw_preserved": sqlite_raw_preserved,
        "sqlite_tender_preserved": sqlite_tender_preserved,
        "normalized_fields_preserved": fields_preserved,
        "documents": dict(doc_metrics), "quality_warnings": warnings,
    }


def empty_source_quality(source_id: str) -> dict:
    return {"source_id": source_id, "run_status": "missing", "coverage_verified_complete": False,
            "records_emitted": 0, "records_seen": 0, "collector_errors": 0,
            "quality": {key: 0 for key in QUALITY_COUNTERS}}


QUALITY_COUNTERS = (
    "candidates", "title_present", "title_missing", "original_title_preserved",
    "direct_tender_url_present", "official_url_host", "official_direct_url_structural",
    "official_direct_url_unverified", "country_valid", "country_preserved",
    "with_email", "with_contact_name", "with_named_person", "with_phone", "without_contacts",
    "with_verified_procurement_email", "with_unverified_email", "with_email_official_evidence",
    "raw_sqlite_preserved", "tender_sqlite_preserved", "normalized_fields_preserved",
    "documents", "pdf_references", "specification_references", "reported_downloaded",
    "saved_files_sha256_verified", "saved_files_unverifiable",
    "downloaded_but_local_file_missing", "saved_files_hash_mismatch", "download_issues",
    "access_restricted_documents", "active", "pending_verification", "history", "excluded",
)


def rollup(rows: list[dict]) -> dict[str, int]:
    stats = Counter()
    for a in rows:
        stats["candidates"] += 1
        for key, flag in (
            ("title_present", "title_present"), ("title_missing", None),
            ("original_title_preserved", "title_preserved"),
            ("direct_tender_url_present", "tender_url_present"),
            ("official_url_host", "tender_url_official_host"),
            ("official_direct_url_structural", "official_direct_url_structural"),
            ("official_direct_url_unverified", None),
            ("country_valid", "country_valid"), ("country_preserved", "country_preserved"),
            ("with_email", "has_email"), ("with_contact_name", "has_contact_name"),
            ("with_named_person", "has_named_person"), ("with_phone", "has_phone"),
            ("without_contacts", "no_contacts"),
            ("with_verified_procurement_email", "has_verified_procurement_email"),
            ("with_unverified_email", "has_unverified_email"),
            ("with_email_official_evidence", "has_email_with_official_evidence"),
            ("raw_sqlite_preserved", "sqlite_raw_preserved"),
            ("tender_sqlite_preserved", "sqlite_tender_preserved"),
            ("normalized_fields_preserved", "normalized_fields_preserved"),
        ):
            value = (not a["title_present"] if key == "title_missing" else
                     a["tender_url_present"] and not a["official_direct_url_structural"]
                     if key == "official_direct_url_unverified" else
                     a[flag])
            stats[key] += int(bool(value))
        for k, v in a["documents"].items():
            stats[k] += v
        stats[a["route"]] += 1
    return {k: stats[k] for k in QUALITY_COUNTERS}
