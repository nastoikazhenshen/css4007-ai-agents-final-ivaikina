#!/usr/bin/env python3
"""Validate independent research final tender JSON with existing checks plus expired bid deadline History gate."""
import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent / 'schemas'
# v1.0 is kept intact; v1.1 adds explicit independent commercial assessment.
COUNTRIES = {
    'ID':'Индонезия', 'TH':'Таиланд', 'TR':'Турция', 'US':'США',
    'MX':'Мексика', 'BR':'Бразилия', 'AR':'Аргентина', 'PE':'Перу',
}
REQUIRED_EVIDENCE = {
    'project.buyer_name', 'project.title',
    'procurement.status', 'procurement.deadline_date',
}
END_STATES = {'closed', 'cancelled', 'awarded'}


def host(url):
    return (urlparse(url).hostname or '').lower().rstrip('.')


def host_allowed(url, allowed):
    hostname = host(url)
    return any(hostname == item.lower() or hostname.endswith('.' + item.lower()) for item in allowed)


def parse_dt(text):
    result = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Datetime must contain UTC offset')
    return result


def expired_submission_with_evidence(record, *, now=None):
    """History may mean no longer open to applications, not formally awarded/closed."""
    procurement = record['procurement']
    deadline = procurement['deadline_date']
    deadline_time = procurement['deadline_time']
    zone_str = procurement['deadline_timezone']
    if not (deadline and deadline_time and zone_str):
        return False
    if not any(e['field_path'] == 'procurement.deadline_date' and e['source_url'] and e['excerpt']
               for e in record['evidence']):
        return False
    try:
        hh, mm = map(int, deadline_time.split(':'))
        zone = ZoneInfo(zone_str)
        end = datetime.combine(date.fromisoformat(deadline), time(hh, mm), tzinfo=zone)
        return end < (now or datetime.now(timezone.utc)).astimezone(zone)
    except (ValueError, ZoneInfoNotFoundError):
        return False


def semantic_errors(record, allowlist, *, now=None):
    err = []
    source = record['source']
    project = record['project']
    procurement = record['procurement']
    assessment = record['assessment']
    decision = assessment['decision']
    now = now or datetime.now(timezone.utc)
    is_v11 = record['schema_version'] == '1.1.0'
    commercial = assessment.get('commercial_relevance') if is_v11 else None
    categories = assessment.get('project_categories', []) if is_v11 else []

    if project['country_name_ru'] != COUNTRIES[project['country_code']]:
        err.append('Country code does not match country name')

    if source['official_url_verified']:
        if not source['tender_url']:
            err.append('official_url_verified requires tender_url')
        elif source['tender_url'] == source['source_url']:
            err.append('tender_url must point to a concrete tender, not source homepage')
        if allowlist is not None:
            allowed = allowlist.get(source['source_id'], [])
            if not allowed:
                err.append('No approved domains configured for source_id')
            elif not host_allowed(source['tender_url'], allowed):
                err.append('Tender URL is outside the approved domain list for this source')
    elif decision == 'active':
        err.append('Active requires confirmed official URL')

    for i, contact in enumerate(record['contacts']):
        if contact['verified'] and not contact['evidence_url']:
            err.append(f'contacts[{i}]: verified contact needs evidence_url')
        if contact['verified'] and not (contact['email'] or contact['official_channel_url']):
            err.append(f'contacts[{i}]: verified contact needs email or official channel')

    if record['specification_status'] == 'found':
        if not any(d['kind'] == 'specification' and d['url'] and d['access'] == 'public' for d in record['documents']):
            err.append('Specification found requires a public specification document URL')

    if is_v11:
        if decision == 'excluded' and not (commercial == 'irrelevant' or project['scale'] == 'small'):
            err.append('Excluded requires confirmed commercial irrelevance or confirmed small scale')
        if commercial == 'irrelevant' and decision not in {'excluded'}:
            err.append('Confirmed commercially irrelevant item must route Excluded')
        if decision == 'excluded' and commercial == 'irrelevant' and not any(
            e['field_path'] == 'assessment.commercial_relevance' and e.get('source_url') and e.get('excerpt')
            for e in record['evidence']
        ):
            err.append('Excluded requires source evidence for commercial irrelevance')
        if decision == 'excluded' and project['scale'] == 'small' and not any(
            e['field_path'] == 'project.scale' and e.get('source_url') and e.get('excerpt')
            for e in record['evidence']
        ):
            err.append('Excluded small-scale decision requires source evidence')
    elif decision == 'excluded' and not (assessment['is_power_line_project'] == 'no' or project['scale'] == 'small'):
        err.append('Legacy Excluded requires non-power-line or small-scale project')

    if decision == 'history' and procurement['status'] in END_STATES and not any(
        e['field_path'] == 'procurement.status' and e.get('source_url') and e.get('excerpt')
        for e in record['evidence']
    ):
        err.append('History lifecycle closure requires official status evidence')
    if decision == 'history' and procurement['status'] not in END_STATES and not expired_submission_with_evidence(record, now=now):
        err.append('History requires confirmed ended lifecycle or evidence-backed expired submission deadline')

    if procurement['status'] in END_STATES and decision not in {'history', 'excluded'}:
        err.append('Confirmed closed/cancelled/awarded tender must go to history (or excluded if irrelevant)')

    if decision == 'active':
        if is_v11:
            if commercial != 'relevant':
                err.append('Active requires confirmed commercial relevance')
            if not categories or not any(c in {'overhead_line','underground_cable','insulators','line_fittings','grid_project'} for c in categories):
                err.append('Active requires a confirmed in-scope commercial category')
            evidence_paths = {e['field_path'] for e in record['evidence'] if e['source_url'] and e.get('excerpt')}
            if not {'assessment.commercial_relevance','assessment.project_categories'} <= evidence_paths:
                err.append('Active requires evidence for commercial relevance and categories')
        elif assessment['is_power_line_project'] != 'yes':
            err.append('Legacy Active requires confirmed power-line project')
        if project['scale'] != 'industrial':
            err.append('Active requires industrial-scale project confirmation')
        if not project['buyer_name'] or not project['title']:
            err.append('Active requires buyer_name and project title')
        if not source['tender_url']:
            err.append('Active requires direct tender URL')
        if allowlist is None:
            err.append('Active requires --domains allowlist at validation time')
        if procurement['status'] != 'open':
            err.append('Active requires official status=open')
        confirmed_email = any(
            c['verified'] and c['email'] and c['evidence_url'] and
            c['kind'] in {'tender_specific', 'procurement_department'}
            for c in record['contacts']
        )
        if not confirmed_email:
            err.append('Active requires verified tender/procurement work email with evidence URL')

        evidence_paths = {e['field_path'] for e in record['evidence']}
        missing = REQUIRED_EVIDENCE - evidence_paths
        if missing:
            err.append('Active lacks per-field evidence: ' + ', '.join(sorted(missing)))

        deadline_s = procurement['deadline_date']
        if not deadline_s:
            err.append('Active requires confirmed deadline_date')
        else:
            try:
                zone_str = procurement['deadline_timezone']
                zone = ZoneInfo(zone_str) if zone_str else timezone.utc
                local_now = now.astimezone(zone)
                deadline = date.fromisoformat(deadline_s)
                if deadline < local_now.date():
                    err.append('Active deadline has already passed')
                elif deadline == local_now.date():
                    deadline_time = procurement['deadline_time']
                    if deadline_time is None or zone_str is None:
                        err.append('Deadline is today but its time/timezone is not verified')
                    else:
                        hh, mm = map(int, deadline_time.split(':'))
                        if datetime.combine(deadline, time(hh, mm), tzinfo=zone) <= local_now:
                            err.append('Deadline time has already passed')
            except (ValueError, ZoneInfoNotFoundError) as exc:
                err.append('Invalid deadline date/time/timezone: ' + str(exc))

        try:
            checked = parse_dt(procurement['status_checked_at'])
            age = now.astimezone(timezone.utc) - checked.astimezone(timezone.utc)
            if age > timedelta(hours=48):
                err.append('Active status was checked more than 48 hours ago')
            elif age < timedelta(minutes=-15):
                err.append('Status check timestamp is in the future')
        except ValueError as exc:
            err.append('Invalid status check timestamp: ' + str(exc))

    return err


def main():
    parser = argparse.ArgumentParser(description='Validate a single tender or a batch of tenders')
    parser.add_argument('input', type=Path)
    parser.add_argument('--domains', type=Path, help='Optional source-id => approved domains JSON. Required for ACTIVE.')
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding='utf-8'))
    version = data.get('schema_version')
    schema_file = (('batch.v1_1.schema.json' if version == '1.1.0' else 'batch.schema.json')
                   if 'records' in data else ('tender.v1_1.schema.json' if version == '1.1.0' else 'tender.schema.json'))
    schema = json.loads((ROOT / schema_file).read_text(encoding='utf-8'))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    problems = [f'JSON Schema: {".".join(str(p) for p in issue.path) or "root"}: {issue.message}'
                for issue in validator.iter_errors(data)]
    if problems:
        print('INVALID\n' + '\n'.join('- ' + p for p in problems))
        return 1

    allowlist = json.loads(args.domains.read_text(encoding='utf-8')) if args.domains else None
    records = data['records'] if 'records' in data else [data]
    seen = set()
    for idx, record in enumerate(records):
        rid = record['record_id']
        if rid in seen:
            problems.append(f'records[{idx}]: duplicate record_id: {rid}')
        seen.add(rid)
        problems.extend(f'records[{idx}] {rid}: {msg}' for msg in semantic_errors(record, allowlist))

    if problems:
        print('INVALID\n' + '\n'.join('- ' + p for p in problems))
        return 1
    print(f'VALID: {len(records)} record(s); schema and semantic checks passed')
    return 0

if __name__ == '__main__':
    sys.exit(main())
