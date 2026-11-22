"""Collector Validation (stage 1): original unchanged JSON Schema plus source-run invariants."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit
from .collector_to_tender import require_schema


def validate_run(run: dict) -> None:
    require_schema(run, 'collector_run.schema.json')
    if len(run['tenders']) != run['metrics']['records_emitted']:
        raise ValueError('CollectorRun emitted count differs from tenders length')
    if run['run_status'] not in ('success', 'partial') and run['tenders']:
        raise ValueError('CollectorRun cannot contain tenders under unsuccessful status')
    if datetime.fromisoformat(run['started_at'].replace('Z', '+00:00')) > datetime.fromisoformat(run['finished_at'].replace('Z', '+00:00')):
        raise ValueError('CollectorRun finished before started')
    seen = set()
    for tender in run['tenders']:
        if tender['country_code'] != run['country_code']:
            raise ValueError('Country mismatch between run and tender')
        # Missing external ID is legal in the canonical schema. Use stable URLs
        # or title/landing-page identity for the candidate; never invent an
        # alleged platform-issued tender number.
        key = (tender.get('source_tender_id') or tender.get('tender_url') or
               (tender.get('listing_url', '') + '|' + (tender.get('title_original') or '')))
        if key in seen:
            raise ValueError('Duplicate candidate identity inside one run')
        seen.add(key)
        # Candidate can have no tender_url, but must have a real landing page
        # for canonical TenderRecord.source.source_url. Missing both is raw inbox.
        listing = tender.get('listing_url')
        url = tender.get('tender_url')
        if not (listing or url):
            raise ValueError('No source URL to support canonical TenderRecord; store in raw inbox')
        for link in (listing, url):
            if link and (urlsplit(link).scheme != 'https' or not urlsplit(link).hostname):
                raise ValueError('Non-HTTPS source link')
