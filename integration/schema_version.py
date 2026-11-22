"""Backward compatible v1.0.0 tender upgrade; no claim of completed AI analysis."""
from __future__ import annotations
from copy import deepcopy
from .commercial import initial_assessment


def upgrade_legacy_tender(record: dict) -> dict:
    if record.get('schema_version') == '1.1.0':
        return deepcopy(record)
    if record.get('schema_version') != '1.0.0':
        raise ValueError('Unsupported TenderRecord version')
    upgraded = deepcopy(record)
    cats, relevance, demand, _ = initial_assessment(
        record['project'].get('title'), record['project'].get('description'))
    upgraded['schema_version'] = '1.1.0'
    # Respect original semantic outcome; do not silently claim relevance confirmed.
    upgraded['assessment'].update({
        'project_categories': cats,
        'commercial_relevance': relevance,
        'product_demand': demand,
    })
    # Previous v1.0 excluded underground-only by now-revoked business policy.
    old_reason = record['assessment'].get('reason', '').lower()
    if record['assessment']['decision'] == 'excluded' and 'underground' in old_reason:
        upgraded['assessment']['decision'] = 'pending_verification'
        upgraded['assessment']['reason'] = 'Reopened after 2026-11 commercial expansion; re-evaluate lifecycle.'
    return upgraded
