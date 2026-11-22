"""Pure SheetsRow projection, without Sheets API or side effects."""
from .collector_to_tender import require_schema
from contracts.validation.validate_tender import semantic_errors

TABS = {
    'active': 'Active Tenders',
    'pending_verification': 'Pending Verification',
    'history': 'Tender History',
}


def project_sheet_row(tender: dict, *, allowlist: dict | None = None, now=None) -> dict | None:
    require_schema(tender, 'tender.v1_1.schema.json' if tender['schema_version'] == '1.1.0' else 'tender.schema.json')
    problems = semantic_errors(tender, allowlist, now=now)
    if problems:
        raise ValueError('Tender business validation failed: ' + '; '.join(problems))
    decision = tender['assessment']['decision']
    if decision == 'excluded':
        return None  # canonical SheetsRow schema intentionally has no Excluded tab
    contacts = [c for c in tender['contacts'] if c['verified'] and c['email']]
    best = contacts[0] if contacts else None
    spec = next((d['url'] for d in tender['documents']
                 if d['kind'] == 'specification' and d['access'] == 'public' and d['url']), None)
    r = {
        'schema_version': '1.0.0', 'record_id': tender['record_id'],
        'target_sheet': TABS[decision],
        'country': tender['project']['country_name_ru'],
        'company': tender['project']['buyer_name'],
        'project': tender['project']['title'],
        'contact_person': (best['name'] or best['role']) if best else None,
        'email': best['email'] if best else None,
        'tender_url': tender['source']['tender_url'],
        'deadline_date': tender['procurement']['deadline_date'],
        'specification_url': spec,
        'status': tender['procurement']['status'],
        'last_checked_at': tender['audit']['last_checked_at'],
    }
    require_schema(r, 'sheets_row.schema.json')
    return r
