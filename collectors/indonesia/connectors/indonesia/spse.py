"""Read public SPSE results through the site's own search box."""
import re
from urllib.parse import urljoin, urlsplit

from lxml import html
from playwright.sync_api import Error

ROOT = "https://spse.inaproc.id/"
QUERIES = ("listrik", "transmisi", "SUTT", "SKTT", "SUTM", "SKTM", "HVDC")


def result_rows(markup, slug):
    page = html.fromstring(markup)
    prefix = rf"/{re.escape(slug)}/lelang/(\d+)"
    rows = []
    for tr in page.xpath('//table[@id="tbllelang"]/tbody/tr'):
        cells = tr.xpath("./td")
        for a in tr.xpath(".//a[@href]"):
            url = urljoin(ROOT, a.get("href"))
            match = re.fullmatch(prefix + "/pengumumanlelang", urlsplit(url).path)
            if urlsplit(url).netloc != urlsplit(ROOT).netloc or not match:
                continue
            schedule = next((urljoin(ROOT, link.get("href")) for link in tr.xpath(".//a[@href]")
                             if link.get("href") == f"/{slug}/lelang/{match[1]}/jadwal"), None)
            rows.append({"id": match[1], "title": a.text_content().strip(), "url": url,
                         "buyer": cells[2].text_content().strip() if len(cells) > 2 else None,
                         "stage": cells[3].text_content().strip() if len(cells) > 3 else "",
                         "schedule": schedule})
            break
    return rows


def search(browser, slug, year, relevant, allowed, max_pages=5):
    index = f"{ROOT}{slug}/lelang?tahun={year}"
    if not allowed(index):
        raise ValueError("robots.txt excludes SPSE index")
    page = browser.new_page()
    rows, errors = {}, []
    successful_queries = 0
    try:
        response = page.goto(index, wait_until="domcontentloaded", timeout=20000)
        if response and response.status in {401, 403}:
            return [], {"status": "access_restricted", "errors": [f"SPSE index HTTP {response.status}"]}
        if response is None or not response.ok:
            raise ValueError(f"SPSE index HTTP {response.status if response else 'unknown'}")
        box = page.locator("#tbllelang_filter input")
        box.wait_for(timeout=15000)
        for term in QUERIES:
            try:
                box.fill(term)
                with page.expect_response(lambda r: f"/{slug}/dt/lelang?" in r.url, timeout=15000) as received:
                    box.press("Enter")
                for count in range(max_pages):
                    result = received.value
                    if result.status in {401, 403}:
                        return [], {"status": "access_restricted", "errors": [f"SPSE search HTTP {result.status}"]}
                    if not result.ok:
                        raise ValueError(f"HTTP {result.status}")
                    payload = result.json()
                    if not isinstance(payload.get("data"), list):
                        raise ValueError("Unexpected SPSE search response")
                    page.locator("#tbllelang_processing").wait_for(state="hidden", timeout=10000)
                    extracted = result_rows(page.content(), slug)
                    if len(extracted) != len(payload["data"]):
                        raise ValueError("SPSE rendered rows do not match search response")
                    rows.update((row["id"], row) for row in extracted)
                    if count == 0:
                        successful_queries += 1
                    nxt = page.locator("#tbllelang_next:not(.disabled) a")
                    if not nxt.count():
                        break
                    if count + 1 == max_pages:
                        errors.append(f"{term}: pagination limit {max_pages}")
                        break
                    with page.expect_response(lambda r: f"/{slug}/dt/lelang?" in r.url, timeout=15000) as received:
                        nxt.click()
            except (Error, ValueError) as exc:
                errors.append(f"{term}: {str(exc).splitlines()[0]}")
        candidates = [row for row in rows.values() if relevant(row["title"])]
        for row in candidates:
            for key in ("url", "schedule"):
                target = row[key]
                if not target or not allowed(target):
                    continue
                try:
                    response = page.goto(target, wait_until="domcontentloaded", timeout=15000)
                    if (response and response.status in {401, 403}) or "/login" in urlsplit(page.url).path:
                        return [], {"status": "access_restricted", "records_loaded": len(rows),
                                    "errors": [f"SPSE {key} HTTP {response.status if response else 'unknown'} or login"]}
                    if response is None or not response.ok:
                        errors.append(f"{row['id']} {key}: HTTP {response.status if response else 'unknown'} or login")
                    elif key == "schedule":
                        row["schedule_html"] = page.content()
                    else:
                        row["detail_html"] = page.content()
                except Error as exc:
                    errors.append(f"{row['id']} {key}: {str(exc).splitlines()[0]}")
        return candidates, {"status": "failed" if not successful_queries else "partial" if errors else "ok",
                            "successful_queries": successful_queries, "records_loaded": len(rows),
                            "candidates": len(candidates), "errors": errors}
    except Error as exc:
        raise ValueError(f"SPSE browser: {str(exc).splitlines()[0]}") from exc
    finally:
        page.close()
