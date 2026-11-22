"""Inspect the two public tender tables and their browser-side transport."""
import re
from urllib.parse import urlsplit, urljoin
import hashlib
from lxml import html

from playwright.sync_api import Error

URL = "https://vms.pln.co.id/"



def listed_rows(markup):
    page = html.fromstring(markup)
    records = []
    for table in page.xpath("//main//table")[:2]:
        for tr in table.xpath(".//tbody/tr"):
            text = " ".join(tr.text_content().split())
            if not text:
                continue
            anchors = tr.xpath(".//a[@href]")
            url = None
            title = text
            for anchor in anchors:
                link = urljoin(URL, anchor.get("href"))
                if urlsplit(link).hostname == "vms.pln.co.id" and "/login" not in urlsplit(link).path:
                    url, title = link, " ".join(anchor.text_content().split()) or text
                    break
            records.append({"id":hashlib.sha256((url or text).encode()).hexdigest()[:24],
                            "title":title,"url":url,"text":text})
    return records

def check(browser):
    page = browser.new_page()
    requests = []

    def record(response):
        if response.request.resource_type in {"xhr", "fetch"}:
            url = urlsplit(response.url)
            requests.append({"host": url.hostname, "path": url.path, "http_status": response.status})

    page.on("response", record)
    page.on("requestfailed", lambda req: requests.append({
        "host": urlsplit(req.url).hostname, "path": urlsplit(req.url).path, "error": req.failure})
        if req.resource_type in {"xhr", "fetch"} else None)
    try:
        response = page.goto(URL, wait_until="domcontentloaded", timeout=20000)
        if response and response.status in {401, 403}:
            return {"status": "access_restricted", "attempts": [{"url": URL, "http_status": response.status}],
                    "notes": "VMS public entry requires access"}
        page.get_by_role("heading", name=re.compile(r"List Pengumuman Pengadaan")).first.wait_for(timeout=15000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Error:
            requests.append({"error": "networkidle timeout"})
        tables = page.locator("main table")
        count = min(tables.count(), 2)
        rows = [tables.nth(i).locator("tbody tr").all_text_contents() for i in range(count)]
        records = [text for table in rows for text in table if text.strip()]
        restricted = "/login" in urlsplit(page.url).path
        failed_requests = any(req.get("error") or req.get("http_status", 0) >= 400 for req in requests)
        return {"status": "access_restricted" if restricted else "partial" if failed_requests else "probe_only" if records else "empty_unverified",
                "rendered_tables": count, "nonempty_rows": len(records), "rows":listed_rows(page.content()), "network_observations": requests,
                "notes": "Выдача KHS/Non-KHS; полнота и связь сетевых ответов со списками закупок не подтверждены."}
    except Error as exc:
        raise ValueError(f"VMS browser: {str(exc).splitlines()[0]}") from exc
    finally:
        page.close()
