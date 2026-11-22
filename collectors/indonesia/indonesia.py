"""Read-only Indonesia tender collectors. Run: python indonesia.py --output run."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from collections import Counter
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import httpx
from lxml import html
from pypdf import PdfReader
from pydantic import AwareDatetime, BaseModel, HttpUrl
from collector_state import State, notice_body
from connectors.indonesia.scope import candidate_reason
from attachments import Documents, links as attachment_links, pdf_links

BATAM = "https://www.plnbatam.com/pengumuman-pengadaan/"
PLN = "https://eproc.pln.co.id/portal/home"
VMS = "https://vms.pln.co.id/"
SPSE = "https://spse.inaproc.id/"
BANK = "https://search.worldbank.org/api/v2/procnotices"
MONTHS = "januari februari maret april mei juni juli agustus september oktober november desember".split()
DATE = re.compile(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(20\d{2})\b", re.I)
TIME = re.compile(r"\b(\d{1,2})[.:](\d{2})\s*(WIB|WITA|WIT)\b", re.I)
ZONES = {"WIB": "Asia/Jakarta", "WITA": "Asia/Makassar", "WIT": "Asia/Jayapura"}
WORK = re.compile(r"pembangunan|konstruksi|rekonstruksi|modernisasi|perluasan|penguatan|"
                  r"re.?route|rekonduktor|uprating|construction|rebuild|reconstruction|upgrade|expansion|"
                  r"rehabilitasi|revitalisasi|penggantian|peningkatan|pemasangan|perbaikan jaringan|"
                  r"pembangunan|rehabilitation|replacement|reconductoring|refurbishment", re.I)
LINE = re.compile(r"\b(?:sutet|sutt|sutm|sutr|sktt|sktm|sktr|hvdc|hvac)\b|"
                  r"saluran (?:kabel|udara|transmisi)|pembangunan transmisi|"
                  r"jaringan (?:transmisi|distribusi|listrik|tegangan (?:menengah|rendah|tinggi))|"
                  r"jalur kabel (?:tm|listrik)|saluran listrik|feeder listrik|"
                  r"(?:transmission|distribution|power) lines?|underground cable", re.I)
OUT = re.compile(r"studi kelayakan|feasibility study|jasa konsult|consulting services|"
                 r"pemeliharaan rutin|fiber opti[ck]|\bfo adss\b|utility box|"
                 r"instalasi gedung|wiring|ground patrol|\bsubduct\b|^pengadaan kabel power", re.I)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
REG = r"pendaftaran (?:dan |serta |peserta|tender)|(?:batas akhir|perpanjangan|penutupan) pendaftaran"
SUBMISSION = r"pemasukan (?:dokumen )?penawaran|batas akhir (?:pemasukan|penyampaian) penawaran|penyampaian dokumen penawaran"
UPDATE = re.compile(r"perpanjangan|perubahan|pembatalan|dibatalkan|add?endum|koreksi|jadwal pengadaan|hasil (?:tender|pengadaan)|\bext\b", re.I)
CANCEL = re.compile(r"pembatalan|dibatalkan", re.I)
RESULT = re.compile(r"hasil (?:tender|pengadaan)", re.I)
INVITE = re.compile(r"mengundang penyedia|mengundang calon penyedia|mengundang peserta", re.I)
ROUTE = re.compile(r"\bt[.\s]*(\d+)\s*[-–—]\s*t[.\s]*(\d+)\b", re.I)
NOTICE_ID = re.compile(r"\bNomor\s*:\s*([\w.\-/]+/20\d{2})", re.I)
SECTION_END = r"\s+[•]\s*|\s+\d{1,2}\.\s+(?=[A-Za-z])"
CONTACT = re.compile(r"pelaksana pengadaan|panitia pengadaan|procurement management|pokja|"
                     r"hubungi|melalui email|dengan alamat email|kontak", re.I)


class Evidence(BaseModel):
    evidence_id: str
    source_url: HttpUrl
    locator: str
    observed_at: AwareDatetime
    original_excerpt: str


class Contact(BaseModel):
    name: str | None = None
    department: str | None = None
    email: str | None = None
    contact_type: str
    evidence_ids: list[str]


class TenderRecord(BaseModel):
    source_id: str
    source_tender_id: str
    country_code: str = "ID"
    buyer_name_original: str | None
    title_original: str
    description_original: str | None = None
    description_normalized: str | None = None
    tender_url: HttpUrl | None
    published_at_original: str | None = None
    deadline_at_utc: AwareDatetime | None = None
    deadline_original: str | None = None
    deadline_timezone: str | None = None
    registration_deadline_at_utc: AwareDatetime | None = None
    bid_deadline_at_utc: AwareDatetime | None = None
    deadline_basis: str | None = None
    entry_status: str = "unknown"
    procurement_status: str = "unknown"
    processing_status: str = "pending_verification"
    contacts: list[Contact]
    documents: list[dict]
    specification_url: HttpUrl | None = None
    spec_status: str = "not_found_public"
    evidence: list[Evidence]
    last_seen_at: AwareDatetime
    last_verified_at: AwareDatetime | None = None
    content_sha256: str
    verification_notes: list[str]


def clean(text: str) -> str:
    return " ".join(text.split())


def relevant(text: str) -> bool:
    return bool(WORK.search(text) and LINE.search(text) and not OUT.search(text))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(url: str, now: datetime, locator: str, quote: str) -> Evidence:
    return Evidence(evidence_id="ev_" + sha((url + locator + quote).encode())[:20],
                    source_url=url, locator=locator, observed_at=now, original_excerpt=quote)


def excerpt(text: str, pattern: str, size: int = 265) -> str:
    m = re.search(pattern, text, re.I)
    if not m:
        return ""
    return re.split(SECTION_END, text[m.start():m.end() + size], maxsplit=1)[0]


def parse_window(block: str):
    dates, times = list(DATE.finditer(block)), list(TIME.finditer(block))
    if not dates or not times:
        return None, None, None
    date = dates[-1]
    # The last date needs its own time. A start time must not become a deadline.
    time = next((x for x in times if x.start() >= date.end()), None)
    if not time:
        return None, None, None
    day, month, year = date.groups()
    hour, minute, zone = time.groups()
    tz = ZONES[zone.upper()]
    try:
        dt = datetime(int(year), MONTHS.index(month.lower()) + 1, int(day), int(hour),
                      int(minute), tzinfo=ZoneInfo(tz))
    except ValueError:
        return None, None, None
    return dt.astimezone(timezone.utc), tz, block[date.start():time.end()]


def pdf_text(blob: bytes, ocr: bool) -> tuple[str, str]:
    reader = PdfReader(io.BytesIO(blob))
    txt = "\n".join(page.extract_text() or "" for page in reader.pages[:5])
    if len(clean(txt)) >= 100 or not ocr or not all(shutil.which(x) for x in ("pdftoppm", "tesseract")):
        return txt, "PDF pp. 1–5"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        file = root / "notice.pdf"
        file.write_bytes(blob)
        subprocess.run(["pdftoppm", "-f", "1", "-l", "2", "-r", "180", "-png", str(file),
                        str(root / "page")], check=True, timeout=20, capture_output=True)
        pages = []
        for image in sorted(root.glob("page-*.png")):
            pages.append(subprocess.run(["tesseract", str(image), "stdout", "-l", "eng"],
                         check=True, timeout=20, capture_output=True, text=True).stdout)
        return "\n".join(pages), "PDF pp. 1–2 (OCR)"


def batam_record(title, description, url, blob, txt, locator, now, updates=()):
    flat = clean(txt)
    evidence = []
    notes = []
    contacts = []
    deadline_block = excerpt(flat, SUBMISSION)
    registration_block = excerpt(flat, REG)
    bid = parse_window(deadline_block)
    registration = parse_window(registration_block)
    trusted = "OCR" not in locator
    bid_trusted = registration_trusted = trusted
    contact_sources = [(url, locator, flat)] if trusted else []
    if bid[0]:
        evidence.append(digest(url, now, locator + " / Pemasukan Penawaran", deadline_block))
    if registration[0]:
        evidence.append(digest(url, now, locator + " / Pendaftaran", registration_block))
    cancelled = completed = unresolved = False
    documents = [{"title": "Pengumuman tender", "source_url": url,
                  "document_kind": "tender_document", "sha256": sha(blob)}] if blob else []
    for update_url, label, update_blob, update_txt, update_locator in updates:
        evidence.append(digest(BATAM, now, "Связанное изменение в ленте PLN Batam", label))
        if update_blob:
            documents.append({"title": label, "source_url": update_url,
                              "document_kind": "tender_update", "sha256": sha(update_blob)})
        cancelled |= bool(CANCEL.search(label))
        completed |= bool(RESULT.search(label))
        if CANCEL.search(label) or RESULT.search(label):
            continue
        change = clean(update_txt)
        new_reg = excerpt(change, REG)
        new_bid = excerpt(change, SUBMISSION)
        update_registration = parse_window(new_reg)
        update_bid = parse_window(new_bid)
        # Ambiguous or competing revisions stay pending until the current schedule can be established.
        if "OCR" in update_locator or len(updates) != 1 or not (update_registration[0] or update_bid[0]):
            unresolved = True
            continue
        if update_registration[0]:
            registration, registration_block = update_registration, new_reg
            registration_trusted = True
            evidence.append(digest(update_url, now, update_locator + " / Pendaftaran", new_reg))
        if update_bid[0]:
            bid, deadline_block = update_bid, new_bid
            bid_trusted = True
            evidence.append(digest(update_url, now, update_locator + " / Pemasukan Penawaran", new_bid))
        contact_sources.insert(0, (update_url, update_locator, change))

    deadlines = [(value, kind, verified) for value, kind, verified in (
        (registration, "registration", registration_trusted), (bid, "bid_submission", bid_trusted)) if value[0]]
    (due, tz, original), basis, due_trusted = (
        min(deadlines, key=lambda item: item[0][0]) if deadlines else ((None, None, None), None, False))
    reg = registration[0]
    if reg and reg <= now:
        notes.append("Срок обязательной регистрации истёк")
    for contact_url, contact_locator, contact_text in contact_sources:
        for m in EMAIL.finditer(contact_text):
            context = contact_text[max(0, m.start()-100):min(len(contact_text), m.end()+60)]
            if CONTACT.search(context) and m[0].lower() not in {c.email.lower() for c in contacts if c.email}:
                e = digest(contact_url, now, contact_locator + " / procurement email", context)
                evidence.append(e)
                contacts.append(Contact(email=m[0], department="Pejabat Pelaksana Pengadaan",
                                        contact_type="tender_department", evidence_ids=[e.evidence_id]))
    if not contacts:
        notes.append("Контакт по закупке не подтверждён")
    if not bid[0]:
        notes.append("Срок подачи предложения не извлечён")
    if not trusted:
        notes.append("Распознанный скан требует проверки")
    state = "cancelled" if cancelled else "closed" if completed else "unknown"
    entry = "unknown"
    if cancelled or completed:
        entry = "closed"
    elif unresolved:
        notes.append("Требуется подтвердить актуальный срок по опубликованному изменению")
    elif due_trusted and due <= now:
        entry = "closed"
        if bid_trusted and bid[0] and bid[0] <= now:
            state = "closed"
    elif due_trusted and INVITE.search(description) and re.search(r"tender terbuka", description, re.I):
        block = registration_block if basis == "registration" else deadline_block
        window = re.split(r"\b(?:s\s*\.\s*d|sd|sampai)\b\.?", block, maxsplit=1, flags=re.I)
        start = parse_window(window[0])[0] if len(window) == 2 else None
        if start and start > now:
            notes.append("Опубликованное окно ещё не началось")
        else:
            state = "open" if bid[0] and bid_trusted and bid[0] > now else "unknown"
            entry = "open"
            evidence.append(digest(BATAM, now, "Публичное приглашение и расписание PLN Batam", description))
    if entry == "unknown":
        notes.append("Возможность вступить в закупку требует проверки")
    published = re.search(r"\bBatam,\s*(" + DATE.pattern + ")", flat, re.I)
    if published and trusted:
        evidence.append(digest(url, now, locator + " / publication", published[0]))
    identifier = next((match for _, _, content in contact_sources if (match := NOTICE_ID.search(content))), None)
    spec = "restricted" if re.search(r"dokumen tender/rks.*(?:verifikasi|terdaftar|pendaftaran)|pendaftaran.*dokumen tender/rks", flat, re.I) else "not_found_public"
    return TenderRecord(source_id="pln_batam", source_tender_id=(identifier[1] if identifier else sha(url.encode())[:24]),
                        buyer_name_original="PT PLN Batam", title_original=title,
                        description_original=description, tender_url=url,
                        published_at_original=published[1] if published and trusted else None,
                        deadline_at_utc=due,
                        deadline_original=original, deadline_timezone=tz, registration_deadline_at_utc=reg,
                        bid_deadline_at_utc=bid[0], deadline_basis=basis, entry_status=entry,
                        procurement_status=state, processing_status=(
                            "archived" if entry == "closed" else "validated" if entry == "open" and contacts else "pending_verification"),
                        contacts=contacts, documents=documents,
                        spec_status=spec, evidence=evidence, last_seen_at=now,
                        last_verified_at=now if due_trusted or (trusted and blob) else None, content_sha256=sha(blob),
                        verification_notes=notes)


def document_text(blob, ocr, state=None):
    digest = sha(blob)
    cached = state.extracted(digest, ocr) if state else None
    if cached:
        return cached
    text, locator = pdf_text(blob, ocr)
    if state:
        state.save_extracted(digest, ocr, text, locator)
    return text, locator


def fetch_batam_pdf(client, robots, url, ocr, state=None):
    if not robots.can_fetch("independent research-TenderScout", url):
        raise ValueError("robots.txt excludes notice PDF")
    cached = state.pdf(url) if state else None
    headers = {}
    if cached:
        headers = {k: v for k, v in zip(("If-None-Match", "If-Modified-Since"), cached[:2]) if v}
    with client.stream("GET", url, headers=headers) as response:
        if response.status_code == 304 and cached:
            blob = cached[2]
            return (blob, *document_text(blob, ocr, state), True)
        response.raise_for_status()
        buffer = bytearray()
        for chunk in response.iter_bytes():
            buffer.extend(chunk)
            if len(buffer) > 8 * 1024 * 1024:
                raise ValueError("PDF exceeds 8 MiB")
    blob = bytes(buffer)
    txt, locator = document_text(blob, ocr, state)
    if state:
        state.save_pdf(url, response, blob)
    return blob, txt, locator, False


def normalized_label(value):
    return " ".join(re.findall(r"\w+", value.casefold()))


def related_update(title, identifier, label, update_url):
    key = normalized_label(title)
    source = label + " " + unquote(urlsplit(update_url).path.rsplit("/", 1)[-1])
    if identifier and identifier.casefold() in source.casefold():
        return True
    if len(key.split()) >= 5 and re.search(rf"\b{re.escape(key)}\b", normalized_label(source)):
        return True
    route = ROUTE.search(title)
    if not route:
        return False
    places = {word for word in normalized_label(title[route.end():]).split() if len(word) >= 5}
    return bool(places.intersection(normalized_label(source).split()) and
                any(match.groups() == route.groups() for match in ROUTE.finditer(source)))


def notice_title(label, description):
    quoted = re.search(r'[“"«]([^”"»]{15,550})[”"»]', description)
    if quoted and candidate_reason(quoted[1]):
        return clean(quoted[1])
    if normalized_label(label) in {"pengumuman", "pengumuman tender", "pengumuman tender terbuka"}:
        subject = re.search(r"((?:Pengadaan|Pembangunan|Rekonduktoring|Pemasangan|Penggantian)\b.+?)(?=\s+di\s+PT\b)",
                            description, re.I)
        if subject:
            return clean(subject[1])
    return label


def batam(client, now, ocr, state=None):
    robots = RobotFileParser()
    r = client.get(urljoin(BATAM, "/robots.txt"))
    if r.status_code in {404, 410}:
        robots.parse([])  # Missing robots.txt does not itself forbid public pages.
    else:
        r.raise_for_status()
        robots.parse(r.text.splitlines())
    if not robots.can_fetch("independent research-TenderScout", BATAM):
        raise ValueError("robots.txt excludes procurement index")
    r = client.get(BATAM)
    r.raise_for_status()
    if state:
        state.raw("pln_batam", BATAM, now, r.content)
    page = html.fromstring(r.content)
    cached_pdfs = 0
    found = {}
    candidates = []
    errors = []
    for a in page.xpath('//a[@href]'):
        url = urljoin(BATAM, a.get("href"))
        parts = urlsplit(url)
        if (parts.netloc != urlsplit(BATAM).netloc or parts.scheme != "https"
                or "/wp-content/uploads/" not in parts.path or not parts.path.lower().endswith(".pdf")):
            continue
        label = clean(a.text_content())
        prev = a.getparent().getprevious()
        description = clean(prev.text_content())[:1400] if prev is not None else label
        if url not in found or (INVITE.search(description) and relevant(description)):
            found[url] = (label, description)
    if not found:
        raise ValueError("PLN Batam procurement document index is empty or HTML changed")
    updates = [(url, label) for url, (label, _) in found.items()
               if UPDATE.search(label + " " + unquote(urlsplit(url).path))]
    update_cache = {}
    for url, (label, description) in found.items():
        if any(url == update_url for update_url, _ in updates):
            continue
        title = notice_title(label, description)
        if not candidate_reason(title, description):
            continue
        blob, txt, locator = b"", "", "PDF"
        try:
            blob, txt, locator, cached = fetch_batam_pdf(client, robots, url, ocr, state)
            cached_pdfs += int(cached)
        except Exception as error:
            errors.append({"url": url, "error": f"{type(error).__name__}: {error}"})
        matching = []
        identifier = NOTICE_ID.search(clean(txt))
        for update_url, update_label in updates:
            if not related_update(title, identifier[1] if identifier else None, update_label, update_url):
                continue
            if update_url not in update_cache:
                try:
                    *pdf, cached = fetch_batam_pdf(client, robots, update_url, ocr, state)
                    cached_pdfs += int(cached)
                    update_cache[update_url] = pdf
                except Exception as error:
                    errors.append({"url": update_url, "error": f"{type(error).__name__}: {error}"})
                    update_cache[update_url] = (b"", "", "PDF")
            matching.append((update_url, update_label, *update_cache[update_url]))
        card = batam_record(title, description, url, blob, txt, locator, now, matching)
        if not blob or any(not update[2] for update in matching):
            card.entry_status = "unknown"
            card.procurement_status = "unknown"
            card.last_verified_at = None
            card.processing_status = "pending_verification"
            card.verification_notes.append("Документ или изменение не получены; карточка сохранена из официального списка")
        if not blob:
            card.documents.append({"title": label, "source_url": url, "document_kind": "tender_document"})
        if blob:
            card.documents.extend(pdf_links(blob, url))
        card.verification_notes.append(candidate_reason(title, description))
        candidates.append(card)
    return candidates, {"source_id": "pln_batam", "url": BATAM, "status": "partial" if errors else "ok",
                        "documents_discovered": len(found), "candidates": len(candidates),
                        "related_updates_checked": len(update_cache), "unchanged_pdf_http_304": cached_pdfs, "errors": errors}


def publication_date(row):
    try:
        return datetime.strptime(row.get("noticedate") or "", "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def bank(client, now, max_pages, state=None):
    records, seen, errors = [], {}, []
    totals = set()
    pages = requested = duplicates = 0
    page_size = 1000
    for page in range(max_pages):
        offset = page * page_size
        requested += 1
        try:
            response = client.get(BANK, params={"format": "json", "rows": page_size, "os": offset,
                                               "project_ctry_name": "Indonesia"})
            response.raise_for_status()
            if state:
                state.raw("world_bank", str(offset), now, response.content)
            data = response.json()
            total = int(data["total"])
            totals.add(total)
            rows = data.get("procnotices", [])
            if isinstance(rows, dict):
                rows = list(rows.values())
            if not isinstance(rows, list) or int(data["os"]) != offset:
                raise ValueError("Unexpected World Bank pagination")
            pages += 1
            for row in rows:
                identifier = row["id"]
                duplicates += int(identifier in seen)
                seen[identifier] = row
            expected = min(page_size, max(0, total - offset))
            if len(rows) != expected:
                raise ValueError(f"Expected {expected} records at offset {offset}, received {len(rows)}")
            if offset + len(rows) >= total:
                break
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
            break

    countries, categories, languages = Counter(), Counter(), Counter()
    dates = []
    screened = Counter()
    for row in seen.values():
        country = row.get("project_ctry_name") or "unknown"
        countries[country] += 1
        if "indonesia" not in country.casefold():
            continue
        categories[row.get("procurement_group") or "unknown"] += 1
        languages[row.get("notice_lang_name") or "unknown"] += 1
        if date := publication_date(row):
            dates.append(date)
        title = clean(row.get("bid_description") or "")
        body = clean(notice_body(row.get("notice_text")))
        title = title or clean(row.get("project_name") or "") or row["id"]
        if not re.fullmatch(r"OP\d+", str(row["id"])):
            continue
        screened["titles_examined"] += 1
        screened["nonempty_descriptions"] += int(bool(body))
        reason = candidate_reason(title, notice_body(row.get("notice_text")), row.get("project_name") or "")
        if not reason:
            continue
        # Keep plausible construction notices even if the title lacks a narrow notice-type label.
        # Relevance and active eligibility are independently verified downstream.
        url = "https://projects.worldbank.org/en/projects-operations/procurement-detail/" + row["id"]
        blob = json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
        evidence, contacts = [], []
        name = clean(row.get("contact_name") or "")
        email = clean(row.get("contact_email") or "")
        email = email if EMAIL.fullmatch(email) else None
        if name or email:
            quote = " | ".join(x for x in (name, email) if x)
            ev = digest(BANK, now, f"$.procnotices[id={row['id']}].contact_*", quote)
            evidence.append(ev)
            contacts.append(Contact(name=name or None, email=email,
                contact_type="tender_person" if name else "tender_department", evidence_ids=[ev.evidence_id]))
        local = clean(row.get("submission_deadline_date") or "")[:10]
        clock = clean(row.get("submission_deadline_time") or "")
        due = tz = original = None
        due_text = excerpt(body, r"bids must be (?:submitted|delivered)|" + SUBMISSION, 230)
        # A timezone must appear in the submission sentence, not in a generic office address.
        zone = re.search(r"\b(WIB|WITA|WIT)\b", due_text, re.I)
        if zone and re.fullmatch(r"\d{1,2}:\d{2}", clock):
            try:
                parsed = datetime.fromisoformat(local + "T" + clock)
                day_en = re.compile(
                    rf"(?:\b{parsed.day}\s+{parsed:%B}\s+{parsed.year}\b|"
                    rf"\b{parsed:%B}\s+{parsed.day},?\s+{parsed.year}\b)", re.I)
                matching_clock = re.compile(rf"\b0?{parsed.hour}[:.]{parsed.minute:02d}\b")
                if day_en.search(due_text) and matching_clock.search(due_text):
                    tz = ZONES[zone[1].upper()]
                    due = parsed.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
                    original = due_text
                    evidence.append(digest(url, now, "submission deadline", due_text))
            except ValueError:
                pass
        closed_by_type = bool(re.search(r"award|cancel", row.get("notice_type") or "", re.I))
        deadline_date = re.match(r"\d{4}-\d{2}-\d{2}", local)
        expired_date = bool(deadline_date and local < now.astimezone(ZoneInfo("Asia/Jakarta")).date().isoformat())
        if closed_by_type or expired_date:
            evidence.append(digest(BANK, now, f"$.procnotices[id={row['id']}] / status",
                                   f"{row.get('notice_type') or ''} | {local}"))
        state = "closed" if closed_by_type or expired_date or (due and due <= now) else "unknown"
        records.append(TenderRecord(source_id="world_bank", source_tender_id=row["id"],
            buyer_name_original=row.get("contact_organization"), title_original=title,
            description_original=body, published_at_original=publication_date(row),
            tender_url=url, deadline_at_utc=due, deadline_timezone=tz, deadline_original=original,
            bid_deadline_at_utc=due, deadline_basis="bid_submission" if due else None,
            entry_status="closed" if state == "closed" else "unknown",
            procurement_status=state, processing_status="archived" if state=="closed" else "pending_verification",
            contacts=contacts, documents=attachment_links(row.get("notice_text") or "", url),
            evidence=evidence, last_seen_at=now, content_sha256=sha(blob),
            verification_notes=[reason] + (["Требуется проверка актуального статуса в карточке заказчика"] if state=="unknown" else [])))
    total = max(totals, default=None)
    if duplicates:
        errors.append(f"World Bank returned {duplicates} overlapping rows")
    if len(totals) > 1:
        errors.append("World Bank total changed during pagination")
    if total is None or len(seen) != total:
        errors.append("World Bank coverage does not reconcile with the API total")
    if any("indonesia" not in country.casefold() for country in countries):
        errors.append("World Bank country filter returned non-Indonesian records")
    return records, {"source_id": "world_bank", "url": BANK,
                     "status": "failed" if not pages else "partial" if errors else "ok",
                     "records_loaded": len(seen), "indonesia_works_checked": categories["CW"],
                     "source_total": total, "pages": pages, "pages_requested": requested,
                     "duplicate_rows": duplicates, "candidates": len(records), "errors": errors,
                     "verified_complete": not errors, "countries": dict(countries),
                     "categories": dict(categories), "languages": dict(languages), "screening": dict(screened),
                     "date_from": min(dates, default=None), "date_to": max(dates, default=None)}


def spse_cards(browser, client, now, slug):
    from connectors.indonesia import spse

    index = SPSE + slug + "/lelang"
    robots = RobotFileParser()
    rules = client.get(SPSE + "robots.txt")
    rules.raise_for_status()
    robots.parse(rules.text.splitlines())
    rows, stats = spse.search(browser, slug, now.astimezone(ZoneInfo("Asia/Jakarta")).year,
                              relevant, lambda url: robots.can_fetch("independent research-TenderScout", url))
    records = []
    for row in rows:
        evidence = [digest(index, now, "SPSE search result", " | ".join(
            filter(None, (row["id"], row["title"], row["buyer"], row["stage"]))))]
        contacts, schedule = [], {}
        detail = row.get("detail_html")
        if detail:
            page = html.fromstring(detail)
            for tr in page.xpath("//tr"):
                cells = tr.xpath("./th|./td")
                if len(cells) < 2:
                    continue
                label, value = (clean(cell.text_content()) for cell in cells[:2])
                if label.casefold() in {"satuan kerja", "instansi", "k/l/pd/instansi lainnya"} and value:
                    row["buyer"] = value
                if not re.search(r"pokja|panitia|kontak|email", label, re.I):
                    continue
                for email in dict.fromkeys(EMAIL.findall(value)):
                    evidence.append(digest(row["url"], now, label, value))
                    contacts.append(Contact(email=email, contact_type="tender_department",
                                            evidence_ids=[evidence[-1].evidence_id]))
        if row.get("schedule_html"):
            for tr in html.fromstring(row["schedule_html"]).xpath("//tr"):
                cells = [clean(c.text_content()) for c in tr.xpath("./td")]
                if len(cells) < 4:
                    continue
                stage, start, end = cells[1:4]
                kind = ("registration" if re.search(r"pendaftaran", stage, re.I) else
                        "bid_submission" if re.search(r"upload dokumen penawaran|"+SUBMISSION, stage, re.I) else None)
                due, zone, original = parse_window(end)
                if kind and due:
                    schedule[kind] = (due, zone, original)
                    evidence.append(digest(row["schedule"], now, stage, f"{start} — {end}"))
        registration = schedule.get("registration", (None, None, None))[0]
        bid = schedule.get("bid_submission", (None, None, None))[0]
        basis = min(schedule, key=lambda k: schedule[k][0]) if schedule else None
        due, zone, original = schedule[basis] if basis else (None, None, None)
        cancelled = bool(re.search(r"\b(?:tender|seleksi) (?:batal|gagal)\b", row["stage"], re.I))
        finished = bool(re.search(r"sudah selesai|penandatanganan kontrak", row["stage"], re.I))
        closed = cancelled or finished or bool(due and due <= now)
        open_stage = bool(re.match(r"pendaftaran|upload dokumen penawaran|pemasukan penawaran", row["stage"], re.I))
        opened = not closed and bool(due and due > now and open_stage)
        records.append(TenderRecord(
            source_id=f"spse_{slug}", source_tender_id=row["id"], title_original=row["title"],
            buyer_name_original=row["buyer"], tender_url=row["url"], contacts=contacts, documents=[],
            evidence=evidence, last_seen_at=now, content_sha256=sha((detail or row["title"]).encode()),
            registration_deadline_at_utc=registration, bid_deadline_at_utc=bid,
            deadline_at_utc=due, deadline_timezone=zone, deadline_original=original, deadline_basis=basis,
            entry_status="closed" if closed else "open" if opened else "unknown",
            procurement_status="cancelled" if cancelled else "closed" if finished else "open" if opened and basis == "bid_submission" else "unknown",
            processing_status="archived" if closed else "validated" if opened and contacts else "pending_verification",
            last_verified_at=now if closed or opened else None,
            verification_notes=[msg for condition, msg in (
                (not detail, "Карточка недоступна"), (not due, "Дедлайн не подтверждён"),
                (not contacts, "Контакт по закупке не подтверждён")) if condition]))
    return records, stats


def probe_spse(client, year, slug):
    index = SPSE + slug + "/lelang"
    robots = RobotFileParser()
    rules = client.get(SPSE + "robots.txt")
    rules.raise_for_status()
    robots.parse(rules.text.splitlines())
    if not robots.can_fetch("independent research-TenderScout", index):
        raise ValueError("robots.txt excludes SPSE index")
    r = client.get(index, params={"tahun": year})
    r.raise_for_status()
    endpoint = re.search(r'url\s*:\s*"([^"]+/dt/lelang[^"]*)"', r.text)
    token = re.search(r"d.authenticityToken\s*=\s*'([^']+)'", r.text)
    if not endpoint or not token or not endpoint[1].startswith(f"/{slug}/dt/lelang?"):
        raise ValueError("SPSE public search form changed")
    url = urljoin(SPSE, endpoint[1])
    if not robots.can_fetch("independent research-TenderScout", url):
        raise ValueError("robots.txt excludes SPSE results")
    params = {"draw": 1, "start": 0, "length": 20, "search[value]": "jaringan listrik",
              "search[regex]": "false", "order[0][column]": 5, "order[0][dir]": "desc",
              "authenticityToken": token[1]}
    for i in range(6):
        prefix = f"columns[{i}]"
        params.update({prefix+"[data]": i, prefix+"[name]": "", prefix+"[searchable]": "true",
                       prefix+"[orderable]": "true", prefix+"[search][value]": "",
                       prefix+"[search][regex]": "false"})
    result = client.post(url, data=params, headers={"Referer": str(r.url),
                         "Origin": SPSE.rstrip("/"), "X-Requested-With": "XMLHttpRequest"})
    if result.is_error:
        raise ValueError(f"SPSE index HTTP {r.status_code}; public search HTTP {result.status_code}")
    payload = result.json()
    if not isinstance(payload.get("data"), list):
        raise ValueError("SPSE public search response changed")
    return {"status": "probe_only", "records_loaded": len(payload["data"]),
            "notes": "Проверка транспорта. Импорт карточек SPSE ещё не включён."}


def diagnose(client, source, url, now):
    r = client.get(url)
    r.raise_for_status()
    shell = b'<div id="app"' in r.content
    return {"status": "empty_unverified", "http_status": r.status_code,
            "requires_javascript": shell, "observed_rows": None,
            "notes": "Статический HTML проверен. Требуется браузер для проверки таблиц, фильтров и запросов."}


def listed_record(source, identifier, title, url, description, buyer, now, listing):
    return TenderRecord(source_id=source, source_tender_id=str(identifier),
        title_original=title, tender_url=url, description_original=description,
        buyer_name_original=buyer, contacts=[], documents=[],
        evidence=[digest(listing, now, "Public procurement list", title + " | " + description)],
        last_seen_at=now, content_sha256=sha(description.encode()),
        verification_notes=["Карточка-кандидат из списка; статус и дополнительные поля требуют проверки"])


def central_pln(client, now, state):
    from connectors.indonesia.pln_eproc import collect
    rows, info = collect(client, now, state)
    records = []
    for row in rows:
        title = row["namaPengadaan"]
        if not candidate_reason(title):
            continue
        # A modal opened by POST has no confirmed bookmarkable tender URL.
        url = next((row[key] for key in ("tenderUrl", "url", "href") if
                    isinstance(row.get(key), str) and row[key].startswith("https://eproc.pln.co.id/")), None)
        records.append(listed_record("pln_eproc", row["noPengadaan"], title, url,
            json.dumps(row,ensure_ascii=False), "PT PLN (Persero)", now, PLN))
    info["candidates"] = len(records)
    return records, info


def vms_result(client, now, browser):
    if browser is None:
        return [], diagnose(client, "pln_vms", VMS, now)
    from connectors.indonesia.pln_vms import check

    info = check(browser)
    rows = info.pop("rows", [])
    records = [listed_record("pln_vms", row["id"], row["title"], row["url"], row["text"],
                             "PT PLN (Persero)", now, VMS)
               for row in rows if candidate_reason(row["title"], row["text"])]
    info["candidates"] = len(records)
    return records, info


def run(output, bank_pages, ocr, browser=None, spse_authorized=False, state=None):
    if state is None:
        raise ValueError("Persistent State is required to retain source data and documents")
    now = datetime.now(timezone.utc)
    sources = []
    records = {}
    with httpx.Client(timeout=httpx.Timeout(18, connect=7), follow_redirects=False,
                      headers={"User-Agent": "independent research-TenderScout/1.0"}) as client:
        downloader = Documents(client, state, output / "documents")
        for source, url, fetch in (
            ("pln_batam", BATAM, lambda: batam(client, now, ocr, state)),
            ("world_bank", BANK, lambda: bank(client, now, bank_pages, state)),
            ("pln_eproc", PLN, lambda: central_pln(client, now, state)),
            ("pln_vms", VMS, lambda: vms_result(client, now, browser)),
            ("spse_esdm", SPSE+"esdm/lelang", lambda: spse_cards(browser, client, now, "esdm") if spse_authorized and browser else ([], {"status": "access_restricted", "notes": "SPSE crawling requires an agreement with INAPROC operator."})),
            ("spse_jabarprov", SPSE+"jabarprov/lelang", lambda: spse_cards(browser, client, now, "jabarprov") if spse_authorized and browser else ([], {"status": "access_restricted", "notes": "SPSE crawling requires an agreement with INAPROC operator."})),
        ):
            try:
                cards, info = fetch()
                for card in cards:
                    card.documents = [downloader.fetch(source, card.source_tender_id, doc) for doc in card.documents]
                info["with_official_url"] = sum(bool(card.title_original and card.tender_url) for card in cards)
                info["with_contacts"] = sum(any(contact.name or contact.email for contact in card.contacts) for card in cards)
                info["with_documents"] = sum(bool(card.documents) for card in cards)
                info["with_downloaded_documents"] = sum(
                    any(doc.get("download_status") == "downloaded" for doc in card.documents) for card in cards)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                cards, info = [], {"status": "failed", "errors": [str(exc)]}
            sources.append({"source_id": source, "url": url, **info})
            state.source_run(source, now.isoformat(), sources[-1])
            for card in cards:
                key = (card.source_id, card.source_tender_id)
                previous = records.get(key)
                if not previous or len(card.evidence) > len(previous.evidence):
                    records[key] = card
            print(json.dumps(sources[-1], ensure_ascii=False), flush=True)
    sheets = {"Active Tenders": [], "Pending Verification": [], "Tender History": []}
    for record in records.values():
        # Final Active eligibility is decided by the central agent.
        tab = "Tender History" if record.entry_status == "closed" else "Pending Verification"
        sheets[tab].append(record.model_dump(mode="json"))
    output.mkdir(parents=True, exist_ok=True)
    if state:
        for src in sources:
            source = src["source_id"]
            src["changes"] = state.merge(source, [r for r in records.values() if r.source_id == source], now)
            state.source_run(source, now.isoformat(), src)
        save(output / "stored_tenders.json", state.all())
    for title, rows in sheets.items():
        save(output / (title.lower().replace(" ", "_") + ".json"), rows)
    save(output / "sources_monitoring.json", sources)
    report = {"observed_at": now.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
              "counts": {k: len(v) for k, v in sheets.items()}, "sources": sources}
    save(output / "last_run.json", report)
    save(output / "tender_record.schema.json", TenderRecord.model_json_schema())
    from contract_export import export_collector_runs
    export_collector_runs(output)  # Validated against central collector_run.schema.json
    print(json.dumps(report["counts"], ensure_ascii=False), flush=True)


def save(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Collect Indonesian line-construction procurement")
    cli.add_argument("--output", type=Path, default=Path("run"))
    cli.add_argument("--bank-pages", type=int, choices=range(1, 16), default=15, metavar="1–15")
    cli.add_argument("--ocr", action="store_true")
    cli.add_argument("--browser", action="store_true", help="Use Chromium for permitted public interfaces (VMS)")
    cli.add_argument("--chromium-executable", help="Path to a system Chromium for --browser")
    cli.add_argument("--spse-authorized", action="store_true", help="Enable SPSE crawler only after obtaining written operator permission; requires --browser")
    cli.add_argument("--state-dir", type=Path, default=Path("state"))
    cli.add_argument("--interval-minutes", type=int, default=0,
                     help="Run repeatedly at this interval; zero runs once")
    args = cli.parse_args()
    if args.interval_minutes < 0:
        cli.error("--interval-minutes must be non-negative")
    if args.spse_authorized and not args.browser:
        cli.error("--spse-authorized requires --browser")
    import time

    state = State(args.state_dir)
    def collect(browser=None):
        while True:
            run(args.output, args.bank_pages, args.ocr, browser, args.spse_authorized, state)
            if not args.interval_minutes:
                return
            time.sleep(args.interval_minutes * 60)

    try:
        if args.browser:
            from playwright.sync_api import Error, sync_playwright

            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(executable_path=args.chromium_executable)
                except Error as exc:
                    print(f"Chromium unavailable: {str(exc).splitlines()[0]}", flush=True)
                    collect()
                else:
                    try:
                        collect(browser)
                    finally:
                        browser.close()
        else:
            collect()
    finally:
        state.close()
