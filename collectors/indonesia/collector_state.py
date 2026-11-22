"""Durable snapshots and conditional PDF cache for the Indonesia collector."""
import gzip
import hashlib
import json
import sqlite3

from lxml import html


class State:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "indonesia.sqlite")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS source_runs (
          source TEXT NOT NULL, started TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(source,started));
        CREATE TABLE IF NOT EXISTS extracted (
          sha256 TEXT NOT NULL, ocr INTEGER NOT NULL, text TEXT NOT NULL, locator TEXT NOT NULL,
          PRIMARY KEY(sha256,ocr));
        CREATE TABLE IF NOT EXISTS documents (
          source TEXT NOT NULL, tender_id TEXT NOT NULL, url TEXT NOT NULL,
          payload TEXT NOT NULL, PRIMARY KEY(source,tender_id,url));

        CREATE TABLE IF NOT EXISTS pdf (
          url TEXT PRIMARY KEY, etag TEXT, modified TEXT, body BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS pdf_versions (
          url TEXT NOT NULL, sha256 TEXT NOT NULL, body BLOB NOT NULL, PRIMARY KEY(url,sha256));
        CREATE TABLE IF NOT EXISTS raw (
          source TEXT NOT NULL, key TEXT NOT NULL, observed TEXT NOT NULL,
          body BLOB NOT NULL, PRIMARY KEY(source,key,observed));
        CREATE TABLE IF NOT EXISTS tenders (
          source TEXT NOT NULL, id TEXT NOT NULL, url TEXT NOT NULL,
          signature TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(source,id));
        CREATE TABLE IF NOT EXISTS changes (
          source TEXT NOT NULL, id TEXT NOT NULL, observed TEXT NOT NULL,
          kind TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(source,id,observed));
        """)

    def raw(self, source, key, now, body):
        compressed = gzip.compress(body, mtime=0)
        with self.db:
            previous = self.db.execute(
                "SELECT body FROM raw WHERE source=? AND key=? ORDER BY observed DESC LIMIT 1", (source, key)).fetchone()
            if previous is None or previous[0] != compressed:
                self.db.execute("INSERT OR REPLACE INTO raw VALUES (?,?,?,?)",
                                (source, key, now.isoformat(), compressed))

    def pdf(self, url):
        return self.db.execute("SELECT etag,modified,body FROM pdf WHERE url=?", (url,)).fetchone()

    def save_pdf(self, url, response, body):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO pdf_versions VALUES (?,?,?)",
                            (url, hashlib.sha256(body).hexdigest(), body))
            self.db.execute("INSERT OR REPLACE INTO pdf VALUES (?,?,?,?)",
                            (url, response.headers.get("ETag"), response.headers.get("Last-Modified"), body))

    def merge(self, source, records, now):
        changes = {"new": 0, "changed": 0, "unchanged": 0}
        with self.db:
            for record in records:
                row = record.model_dump(mode="json")
                key = (source, row["source_tender_id"])
                url = row["tender_url"] or "missing-url:" + key[1]
                prev = self.db.execute("SELECT id,signature,payload FROM tenders WHERE source=? AND (id=? OR url=?) ORDER BY id=? DESC LIMIT 1",
                                       (*key, url, key[1])).fetchone()
                if prev and any("не получены" in note for note in row.get("verification_notes", [])):
                    old = json.loads(prev[2])
                    for field in ("contacts", "documents", "published_at_original", "description_original"):
                        if not row.get(field):
                            row[field] = old.get(field)
                    old_evidence = {ev["evidence_id"]: ev for ev in old.get("evidence", [])}
                    old_evidence.update({ev["evidence_id"]: ev for ev in row["evidence"]})
                    row["evidence"] = list(old_evidence.values())
                stable = {k: v for k, v in row.items() if k not in {"last_seen_at", "last_verified_at", "evidence"}}
                stable["documents"] = [
                    {k:v for k,v in doc.items() if k not in {"local_path","http_status"}}
                    for doc in (row.get("documents") or [])]
                stable["evidence"] = [{k: v for k, v in ev.items() if k != "observed_at"} for ev in row["evidence"]]
                signature = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
                kind = "new" if prev is None else "changed" if prev[1] != signature else "unchanged"
                changes[kind] += 1
                if prev and prev[0] != key[1]:
                    self.db.execute("DELETE FROM tenders WHERE source=? AND id=?", (source, prev[0]))
                payload = json.dumps(row, ensure_ascii=False)
                self.db.execute("INSERT OR REPLACE INTO tenders VALUES (?,?,?,?,?)", (*key, url, signature, payload))
                if kind != "unchanged":
                    self.db.execute("INSERT OR REPLACE INTO changes VALUES (?,?,?,?,?)",
                                    (*key, now.isoformat(), kind, payload))
        return changes

    def source_run(self, source, started, report):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO source_runs VALUES (?,?,?)",
                            (source,started,json.dumps(report,ensure_ascii=False)))

    def extracted(self, digest, ocr):
        return self.db.execute("SELECT text,locator FROM extracted WHERE sha256=? AND ocr=?",
                               (digest,int(ocr))).fetchone()

    def save_extracted(self, digest, ocr, text, locator):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO extracted VALUES (?,?,?,?)",
                            (digest,int(ocr),text,locator))

    def document(self, source, identifier, result):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?)",
                            (source, identifier, result["source_url"], json.dumps(result, ensure_ascii=False)))

    def all(self):
        return [json.loads(payload) for (payload,) in self.db.execute("SELECT payload FROM tenders ORDER BY source,id")]

    def close(self):
        self.db.close()


def notice_body(value):
    try:
        return html.fromstring(value or "<p></p>").text_content()
    except (ValueError, TypeError):
        return str(value or "")
