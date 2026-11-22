"""Download only explicitly linked official attachments with an auditable outcome."""
import io
import hashlib
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from lxml import html
from pypdf import PdfReader

OFFICIAL = ("plnbatam.com", "pln.co.id", "worldbank.org")
EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".csv", ".xml")


def official(url):
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and not parts.username and not parts.password and (
        any(host == d or host.endswith("." + d) for d in OFFICIAL))


def links(text, base):
    try:
        page = html.fromstring(text or "<p></p>")
    except (ValueError, TypeError):
        return []
    result = []
    for a in page.xpath("//a[@href]"):
        url = urljoin(base, a.get("href"))
        if official(url) and urlsplit(url).path.lower().endswith(EXTENSIONS):
            result.append({"title": " ".join(a.text_content().split()) or "Official attachment",
                           "source_url": url, "document_kind": "attachment"})
    for match in re.finditer(r"https://[^\s<>\"']+?\.(?:pdf|docx?|xlsx?|zip|rar|csv|xml)(?:\?[^\s<>\"']*)?", page.text_content(), re.I):
        url = match[0].rstrip(".,;)")
        if official(url):
            result.append({"title":"Official attachment", "source_url":url, "document_kind":"attachment"})
    return list({doc["source_url"]: doc for doc in result}.values())


def pdf_links(blob, base):
    found = []
    try:
        reader = PdfReader(io.BytesIO(blob))
        for page in reader.pages[:5]:
            for annotation in page.get("/Annots", []):
                action = annotation.get_object().get("/A", {})
                if action.get("/URI"):
                    url = urljoin(base, str(action["/URI"]))
                    if official(url) and urlsplit(url).path.lower().endswith(EXTENSIONS):
                        found.append({"title":"Official PDF attachment", "source_url":url,
                                      "document_kind":"attachment"})
    except Exception:
        return []
    return list({doc["source_url"]:doc for doc in found}.values())


class Documents:
    def __init__(self, client, state, directory):
        self.client, self.state, self.directory = client, state, directory
        directory.mkdir(parents=True, exist_ok=True)
        self.robots = {}
        self.fetched = {}

    def allowed(self, url):
        parts = urlsplit(url)
        origin = parts.scheme + "://" + parts.netloc
        if origin not in self.robots:
            response = self.client.get(origin + "/robots.txt")
            parser = RobotFileParser()
            if response.status_code in {404, 410}:
                parser.parse([])
            elif response.is_success and "<html" not in response.text[:100].lower():
                parser.parse(response.text.splitlines())
            else:
                raise ValueError(f"robots.txt unconfirmed HTTP {response.status_code}")
            self.robots[origin] = parser
        return self.robots[origin].can_fetch("independent research-TenderScout", url)

    def fetch(self, source, identifier, doc):
        url = doc["source_url"]
        if url in self.fetched:
            return self.persist(source, identifier, dict(doc, **self.fetched[url]))
        result = dict(doc, download_status="not_downloaded", download_reason=None)
        try:
            if not official(url):
                raise ValueError("Attachment host is not in official allowlist")
            cached = self.state.pdf(url)
            if cached:
                body = cached[2]
            else:
                if not self.allowed(url):
                    raise ValueError("robots.txt excludes attachment")
                with self.client.stream("GET", url) as response:
                    result["http_status"] = response.status_code
                    if response.status_code in {401, 403}:
                        result.update(download_status="access_restricted", download_reason=f"HTTP {response.status_code}")
                        return self.persist(source, identifier, result)
                    if response.is_redirect:
                        raise ValueError("Redirect requires verification of destination")
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 15 * 1024 * 1024:
                            raise ValueError("Attachment exceeds 15 MiB")
                    body = bytes(body)
                    content_type = response.headers.get("Content-Type", "").lower()
                    if not body or "text/html" in content_type or body.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                        raise ValueError("Received HTML/login/empty response instead of attachment")
                    if urlsplit(url).path.lower().endswith(".pdf") and not body.startswith(b"%PDF"):
                        raise ValueError("PDF signature missing")
            digest = hashlib.sha256(body).hexdigest()
            suffix = Path(urlsplit(url).path).suffix.lower()
            if suffix not in EXTENSIONS:
                suffix = ".bin"
            path = self.directory / (digest + suffix)
            if not path.exists():
                path.write_bytes(body)
            result.update(download_status="downloaded", sha256=digest, local_path=str(path))
        except (httpx.HTTPError, ValueError, OSError) as error:
            result["download_reason"] = str(error)
        return self.persist(source, identifier, result)

    def persist(self, source, identifier, result):
        self.state.document(source, identifier, result)
        self.fetched[result["source_url"]] = {
            k: v for k, v in result.items() if k not in {"title", "source_url", "document_kind"}}
        return result
