"""Two-level validation and idempotent candidate storage. No external API calls."""
from __future__ import annotations

from datetime import datetime
from jsonschema import Draft202012Validator, FormatChecker
from .collector_validation import validate_run
from .collector_to_tender import to_tender, require_schema
from contracts.validation.validate_tender import semantic_errors
from integrations.google_sheets.port import prepare_upsert


def process_run(run: dict, *, inbox, now: datetime | None = None):
    validate_run(run)  # Level 1: collector contract + run invariants.
    result = []
    for notice in run['tenders']:
        tender = to_tender(run, notice, now=now)
        require_schema(tender, 'tender.v1_1.schema.json')
        errors = semantic_errors(tender, None, now=now)  # Level 2: tender + business.
        if errors:
            raise ValueError('Tender Validation: ' + '; '.join(errors))
        inbox.upsert(notice, tender)
        op = prepare_upsert(tender, now=now)
        result.append((tender, op))
    return result
