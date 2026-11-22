"""Durable MVP candidate inbox, for lossless raw-data retention before enrichment.

SQLite used only for the portable MVP integration proof. Production port ->
PostgreSQL transactional storage. Never deletes candidates on a failed source run.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable


class CandidateInbox:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path))
        self.db.execute('''CREATE TABLE IF NOT EXISTS candidate_inbox (
           record_id TEXT PRIMARY KEY,
           source_id TEXT NOT NULL,
           state TEXT NOT NULL,
           raw_json TEXT NOT NULL,
           tender_json TEXT NOT NULL,
           updated_at TEXT NOT NULL
        )''')
        self.db.commit()

    def upsert(self, raw_notice: dict, tender: dict) -> None:
        rid = tender['record_id']
        self.db.execute('''INSERT INTO candidate_inbox
          (record_id,source_id,state,raw_json,tender_json,updated_at)
          VALUES (?, ?, ?, ?, ?, ?)
          ON CONFLICT(record_id) DO UPDATE SET
             state=excluded.state, raw_json=excluded.raw_json,
             tender_json=excluded.tender_json, updated_at=excluded.updated_at
          WHERE candidate_inbox.state != excluded.state
             OR candidate_inbox.raw_json != excluded.raw_json
             OR candidate_inbox.tender_json != excluded.tender_json''',
          (rid, tender['source']['source_id'], tender['assessment']['decision'],
           json.dumps(raw_notice, ensure_ascii=False), json.dumps(tender, ensure_ascii=False),
           tender['audit']['last_checked_at']))
        self.db.commit()

    def get(self, record_id: str) -> dict | None:
        row = self.db.execute('SELECT tender_json FROM candidate_inbox WHERE record_id=?', (record_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def count(self):
        return self.db.execute('SELECT COUNT(*) FROM candidate_inbox').fetchone()[0]

    def close(self):
        self.db.close()
