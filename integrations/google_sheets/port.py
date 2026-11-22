"""Google Sheets delivery port; all rows must be independently validated first."""
from __future__ import annotations

from typing import Protocol

from integration.sheets_projection import project_sheet_row


class SheetsUpserter(Protocol):
    def upsert_by_record_id(self, tab: str, record_id: str, row: dict) -> None:
        """Idempotent upsert; implementation must never append without checking key."""
        ...


def prepare_upsert(tender: dict, *, allowlist: dict | None = None, now=None):
    """Pure function. Does not contact Google Sheets, cannot bypass Active gates."""
    row = project_sheet_row(tender, allowlist=allowlist, now=now)
    if row is None:
        return None  # Excluded records remain in database only.
    return {'operation': 'upsert_by_record_id', 'tab': row['target_sheet'],
            'record_id': row['record_id'], 'row': row}
