"""Check published central PLN pages with bounded retries."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import sleep

import httpx

URLS = ("https://eproc.pln.co.id/portal/home", "https://eproc.pln.co.id/portal/homepage")


def check(client, pause=sleep):
    attempts = []
    for index, url in enumerate((*URLS, URLS[0])):
        try:
            response = client.get(url)
        except httpx.HTTPError as error:
            attempts.append({"url": url, "error": str(error)})
            if index < 2:
                pause(2 ** (index + 1))
            continue
        attempts.append({"url": url, "http_status": response.status_code})
        if response.is_success:
            return {"status": "probe_only", "attempts": attempts,
                    "notes": "Публичный HTML получен. Поток закупок пока не подтверждён."}
        if response.status_code == 401 or response.status_code == 403:
            return {"status": "access_restricted", "attempts": attempts, "notes": "PLN denied public access"}
        if response.status_code == 429:
            break
        if response.status_code == 503 and index < 2:
            hint = response.headers.get("Retry-After")
            seconds = 2 ** (index + 1)
            if hint:
                try:
                    wait = int(hint) if hint.isdigit() else (
                        parsedate_to_datetime(hint) - datetime.now(timezone.utc)).total_seconds()
                    seconds = max(seconds, wait)
                except (ValueError, TypeError):
                    pass
            if seconds > 15:
                return {"status": "failed", "attempts": attempts, "retry_after": hint}
            pause(seconds)
    return {"status": "failed", "attempts": attempts}


def collect(client, now, state, max_pages=10):
    """Read only endpoints explicitly advertised by the public portal JavaScript."""
    import json
    import re
    from urllib.parse import urljoin
    from lxml import html

    report = check(client)
    if report["status"] != "probe_only":
        return [], report
    url = next(row["url"] for row in report["attempts"] if row.get("http_status") == 200)
    response = client.get(url)
    response.raise_for_status()
    state.raw("pln_eproc", url, now, response.content)
    page = html.fromstring(response.content)
    scripts = page.xpath('//script[contains(@src,"pengumumanPengadaan.js")]/@src')
    if not scripts:
        return [], dict(report, errors=["Public procurement script not found"])
    script_url = urljoin(url, scripts[0])
    script = client.get(script_url)
    script.raise_for_status()
    state.raw("pln_eproc", script_url, now, script.content)
    endpoints = list(dict.fromkeys(re.findall(r'BASE_URL\s*\+\s*"(portal/pengumuman_pengadaan/alldata(?:khs)?)"', script.text)))
    rows, errors = {}, []
    received = requested = 0
    for endpoint in endpoints:
        for page_number in range(max_pages):
            requested += 1
            try:
                result = client.get(urljoin(url, "/" + endpoint), params={
                    "draw":page_number+1, "start":page_number*100, "length":100, "mysearch":"",
                    "kd_wilayah":"", "tgl_awal":"", "tgl_akhir":""},
                    headers={"Referer":url,"X-Requested-With":"XMLHttpRequest"})
                report["attempts"].append({"url":str(result.url),"http_status":result.status_code})
                if result.status_code in {401, 403}:
                    return list(rows.values()), dict(report, status="access_restricted",
                        pages=received, pages_requested=requested, records_loaded=len(rows),
                        errors=errors, notes="PLN denied public list access")
                result.raise_for_status()
                state.raw("pln_eproc", str(result.url), now, result.content)
                payload = result.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                    raise ValueError("Unexpected PLN list response")
                items = payload["data"].get("pengumumanPengadaanList")
                if items is None:
                    raise ValueError("Empty/null list cannot confirm procurement coverage")
                if not isinstance(items, list):
                    raise ValueError("Unexpected PLN list response")
                received += 1
                for item in items:
                    if item.get("noPengadaan") and item.get("namaPengadaan"):
                        rows[str(item["noPengadaan"])] = item
                if len(items) < 100:
                    break
                if page_number+1 == max_pages:
                    errors.append("PLN pagination limit reached")
            except (httpx.HTTPError, ValueError, TypeError) as error:
                errors.append(f"{endpoint}: {error}")
                break
    return list(rows.values()), dict(report, status="partial" if rows else "empty_unverified",
        pages=received, pages_requested=requested, records_loaded=len(rows), errors=errors,
        notes="Public list transport checked; completeness and direct card URLs need verification")
