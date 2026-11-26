"""Persistent, evidence-aware registry bridging collectors, AI tools and planned projects.

This is an additive SQLite pilot. It never replaces the canonical CollectorRun,
TenderRecord, central business validator or country-specific collectors.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from integration.collector_to_tender import require_schema, to_tender
from integration.collector_validation import validate_run
from contracts.validation.validate_tender import semantic_errors


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def _sha(data):
    return hashlib.sha256(_json(data).encode('utf-8')).hexdigest()


def _str(value):
    return str(value).strip() if value is not None else ''


def _bool(value):
    return str(value).strip().lower() in {'1', 'true', 'yes'}


def _public_url(url):
    # Export is a presentation boundary. Keep originals in raw/observations;
    # redact known token query keys and refuse unsafe schemes in public CSV.
    if not isinstance(url,str) or not url.startswith('https://'):
        return None
    parts=urlsplit(url)
    if not parts.hostname or parts.username or parts.password:
        return None
    try:
        from collectors.common.runtime import safe_url
        return safe_url(url)
    except ImportError:
        return url


def _day(value):
    try:
        return date.fromisoformat(value) if value else None
    except (ValueError, TypeError):
        return None


def _evidence(record):
    return any(x.get('field_path') == 'procurement.deadline_date'
               and x.get('source_url', '').startswith('https://')
               and _str(x.get('excerpt')) for x in record.get('evidence', []))


def _source_deadline_evidence(raw, *, now):
    """Conservative source-native deadline hint when legacy TenderRecord has no date.

    Country connectors retain `deadline_text` and may independently prove a
    bid-submission window. This adds a discoverable *lead*, never Active.
    Source submission proof must be recent, identity-bound in the connector,
    and the earliest plausible UTC cutoff must still be in the future.
    """
    if not isinstance(raw,dict):return None
    text=raw.get('deadline_text')
    if not isinstance(text,str) or not text.strip():return None
    try:
        value=datetime.fromisoformat(text.strip().replace('Z','+00:00'))
    except ValueError:
        return None
    if value.tzinfo is not None:
        earliest=latest=value.astimezone(timezone.utc)
    else:
        earliest=(value-timedelta(hours=14)).replace(tzinfo=timezone.utc)
        latest=(value+timedelta(hours=12)).replace(tzinfo=timezone.utc)
    verification=(raw.get('raw_payload') or {}).get('verification') or {}
    fresh=False
    if verification.get('submission_window_verified_open') is True and verification.get('proof_url'):
        try:
            observed=datetime.fromisoformat(str(verification['checked_at']).replace('Z','+00:00'))
            fresh=bool(observed.tzinfo and timedelta(0)<=now-observed<=timedelta(hours=48))
        except (KeyError,TypeError,ValueError):pass
    if latest<=now:return {'bucket':'past_deadline_review','raw':text,'source_proven':fresh}
    if earliest>now:
        return {'bucket':'future_bid_source_verified' if fresh else 'future_bid_unverified',
                'raw':text,'source_proven':fresh}
    return {'bucket':'today_time_unverified','raw':text,'source_proven':False}


def deadline_bucket(record, *, now: datetime | None = None, raw: dict | None = None):
    """Display eligibility, *not* central tender status.

    A provided date isn't necessarily a proven bid submission deadline.
    Full date+time+IANA timezone permits precise expiry determination.
    Date-only today stays ambiguous. An unproven past date is never Active.
    """
    now = now or utcnow()
    if now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    if record.get('record_type') == 'planned_project_proposal':
        return 'planned_project'
    a, p = record.get('assessment', {}), record.get('procurement', {})
    if a.get('decision') == 'excluded':
        return 'excluded'
    if a.get('decision') == 'history' or p.get('status') in {'awarded', 'closed', 'cancelled'}:
        return 'history'
    bid = _day(p.get('deadline_date'))
    if bid is None:
        hint=_source_deadline_evidence(raw,now=now)
        return hint['bucket'] if hint else 'deadline_unknown'
    proof = _evidence(record)
    zone_name, clock = p.get('deadline_timezone'), p.get('deadline_time')
    if zone_name and clock:
        try:
            zone = ZoneInfo(zone_name)
            hh, mm = [int(n) for n in clock.split(':')]
            deadline = datetime.combine(bid, time(hh, mm), zone)
            if deadline <= now.astimezone(zone):
                return 'past_deadline_review'  # unless central History already established
            return 'future_bid_evidenced' if proof else 'future_bid_unverified'
        except (ValueError, ZoneInfoNotFoundError, TypeError):
            pass
    # With missing timezone, a local date is not an exact instant. Even if
    # tomorrow, it belongs in pending validation until official corroboration.
    # For dates definitely before UTC today, it cannot be a future bid.
    if bid < now.date():
        return 'past_deadline_review'
    if bid == now.date():
        return 'today_time_unverified'
    return 'future_bid_unverified'


class UnifiedRegistry:
    """Durable candidate inventory; no destructive synchronization on empty runs."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS source_runs (
          run_id TEXT PRIMARY KEY, source_id TEXT, country TEXT,
          status TEXT, seen_at TEXT, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS leads (
          record_id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT,
          country TEXT, revision_hash TEXT, first_seen TEXT,
          last_seen TEXT, raw_json TEXT, canonical_json TEXT,
          source_tier INTEGER NOT NULL DEFAULT 0,
          validation_error TEXT
        );
        CREATE TABLE IF NOT EXISTS candidate_inbox (
          record_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
          state TEXT NOT NULL, raw_json TEXT NOT NULL,
          tender_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS observations (
          record_id TEXT NOT NULL, revision_hash TEXT NOT NULL,
          observed_at TEXT NOT NULL, raw_json TEXT NOT NULL,
          canonical_json TEXT, validation_error TEXT,
          PRIMARY KEY (record_id, revision_hash)
        );
        CREATE TABLE IF NOT EXISTS research (
          record_id TEXT NOT NULL, evidence_hash TEXT NOT NULL,
          researched_at TEXT NOT NULL, tool_name TEXT NOT NULL,
          status TEXT, proposal_json TEXT NOT NULL,
          PRIMARY KEY (record_id, evidence_hash)
        );
        ''')
        self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.db.close()

    def _store(self, record_id, kind, source_id, country, original, canonical,
               observed_at, *, error=None, tier=2):
        """Keep every revision; report-only imports never overwrite a raw source.

        Raw notice and normalized record are immutable per content revision. If
        a later observation is empty or less informative we retain all earlier
        evidence; the newest full capture is canonical but no source data lost.
        """
        digest = _sha({'kind': kind, 'original': original, 'normalized': canonical})
        prior = self.db.execute('SELECT revision_hash, source_tier, last_seen, kind FROM leads WHERE record_id=?',
                                (record_id,)).fetchone()
        self.db.execute('''INSERT OR IGNORE INTO observations
          (record_id,revision_hash,observed_at,raw_json,canonical_json,validation_error)
          VALUES (?,?,?,?,?,?)''', (record_id, digest, observed_at, _json(original),
                                 _json(canonical) if canonical is not None else None, error))
        inserted = not bool(prior)
        replace = bool(prior and (tier > prior[1] or (tier == prior[1] and observed_at >= (prior[2] or ''))))
        if prior and prior[3]=='tender' and kind=='quarantine':
            # A temporary parse error never overwrites the previously
            # schema-valid card; new broken raw remains in observations.
            replace=False
        updated = bool(replace and prior[0] != digest)
        if inserted:
            self.db.execute('''INSERT INTO leads
                (record_id,kind,source_id,country,revision_hash,first_seen,last_seen,
                 raw_json,canonical_json,source_tier,validation_error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (record_id,kind,source_id,country,digest,observed_at,observed_at,
                 _json(original),_json(canonical) if canonical is not None else None,tier,error))
        elif tier >= prior[1]:
            # Comparing timestamps avoids overwriting a more recent capture with
            # an older replay. Preserve first_seen forever.
            latest = max(prior[2] or '', observed_at)
            if updated:
                self.db.execute('''UPDATE leads SET kind=?,source_id=?,country=?,
                    revision_hash=?,last_seen=?,raw_json=?,canonical_json=?,
                    source_tier=?,validation_error=? WHERE record_id=?''',
                    (kind,source_id,country,digest,latest,_json(original),
                     _json(canonical) if canonical is not None else None,
                     tier,error,record_id))
            else:
                self.db.execute('UPDATE leads SET last_seen=? WHERE record_id=?', (latest,record_id))
        self.db.commit()
        return ('inserted' if inserted else 'updated' if updated else 'unchanged')

    def ingest_run(self, run: dict, *, now: datetime | None = None):
        """Accept all candidate notices individually; quarantine bad items, never drop.

        The original input is stored before stage-one validation, and every
        notice is saved before normalization. Stage-two invalid records remain
        visible in review and cannot be falsely promoted to Active.
        """
        stamp = run.get('finished_at') or utcnow().isoformat()
        identity = run.get('run_id') or 'invalid_run_' + _sha(run)[:24]
        self.db.execute('INSERT OR IGNORE INTO source_runs VALUES (?,?,?,?,?,?)',
                        (identity,run.get('source_id'),run.get('country_code'),
                         run.get('run_status','unknown'),stamp,_json(run)))
        self.db.commit()
        errors = []
        run_valid=True
        try:
            validate_run(run)
        except Exception as exc:
            run_valid=False
            errors.append('run: ' + type(exc).__name__ + ': ' + str(exc)[:350])
        count = Counter()
        notices=run.get('tenders') or []
        if not isinstance(notices,list):
            errors.append('tenders field not an array; original CollectorRun preserved')
            notices=[]
        for index, raw in enumerate(notices):
            rid = None
            tender = None
            error = None
            try:
                if not run_valid:
                    raise ValueError('CollectorRun stage-one validation failed; raw notice preserved')
                tender = to_tender(run, raw, now=now)
                rid = tender['record_id']
                require_schema(tender, 'tender.v1_1.schema.json')
                issues = semantic_errors(tender, None, now=now)
                if issues:
                    raise ValueError('business validation: ' + '; '.join(issues))
            except Exception as exc:
                error = type(exc).__name__ + ': ' + str(exc)[:350]
                # Preserve exact record/source identity when stage 1 is broken.
                if not rid and isinstance(raw,dict):
                    identity=(raw.get('source_tender_id') or raw.get('tender_url') or
                              f"{raw.get('listing_url')}:{raw.get('title_original')}")
                    if identity and run.get('source_id'):
                        rid='tender_'+hashlib.sha256(
                            f"{run['source_id']}:{identity}".encode()).hexdigest()[:24]
                rid = rid or 'quarantine_' + _sha({'source_id':run.get('source_id'),
                      'index':index, 'raw':raw})[:30]
                tender = None
                errors.append(f'{rid}: {error}')
            self._store(rid, 'tender' if tender else 'quarantine', run.get('source_id'),
                        run.get('country_code'), raw, tender, stamp, error=error)
            if tender is not None:
                # Same canonical inbox contract as integration.candidate_store.
                self.db.execute('''INSERT INTO candidate_inbox
                  (record_id,source_id,state,raw_json,tender_json,updated_at)
                  VALUES (?,?,?,?,?,?)
                  ON CONFLICT(record_id) DO UPDATE SET
                    state=excluded.state, raw_json=excluded.raw_json,
                    tender_json=excluded.tender_json, updated_at=excluded.updated_at
                  WHERE candidate_inbox.raw_json!=excluded.raw_json
                     OR candidate_inbox.tender_json!=excluded.tender_json''',
                  (rid,run.get('source_id'),tender['assessment']['decision'],
                   _json(raw),_json(tender),tender['audit']['last_checked_at']))
                self.db.commit()
            count['received'] += 1
            count['valid' if tender else 'quarantined'] += 1
        return {'source_id':run.get('source_id'), 'run_status':run.get('run_status'),
                'received':count['received'], 'valid':count['valid'],
                'quarantined':count['quarantined'], 'errors':errors,
                'retained_total':self.count()}

    def ingest_plan(self, plan: dict):
        from collectors.planning.models import validate
        validate(plan)
        # Platform project IDs don't enter TenderRecord; never auto-promote plans.
        rid = 'plan_' + str(plan['project_key'])
        return self._store(rid, 'planned_project',plan.get('source_id'),
                           plan.get('country_code'),plan,plan,
                           plan.get('fetched_at') or utcnow().isoformat())

    def ingest_plan_file(self, path):
        items = json.loads(Path(path).read_text(encoding='utf-8'))
        if isinstance(items,dict):
            items = items.get('projects', [])
        report=Counter()
        for plan in items:
            try:report[self.ingest_plan(plan)]+=1
            except Exception as exc:
                rid='plan_quarantine_'+_sha(plan)[:25]
                report[self._store(rid,'quarantine_plan',plan.get('source_id'),
                         plan.get('country_code'),plan,None,utcnow().isoformat(),
                         error=type(exc).__name__+': '+str(exc)[:350])]+=1
        return dict(report)

    def import_summary_csv(self, path, *, kind='tender'):
        """Restore prior audit inventories as unverified leads, never Active.

        The original CSV is a *report*, not a canonical CollectorRun. The raw
        fields are kept with tier=0 and cannot supersede a schema-valid record.
        """
        if kind not in {'tender','planned_project'}:
            raise ValueError('Unknown summary kind')
        counter=Counter()
        with open(path,encoding='utf-8-sig',newline='') as fh:
            for row in csv.DictReader(fh):
                rid=_str(row.get('record_id'))
                if not rid:
                    rid='summary_'+_sha(row)[:24]
                if kind=='planned_project':
                    rid = 'plan_' + rid.removeprefix('plan_')
                bucket='reported_plan' if kind=='planned_project' else 'reported_tender'
                record={'record_type':bucket,'record_id':rid,'source_report':str(Path(path).name),
                        'country':row.get('country') or row.get('country_code'),
                        'title_original':row.get('title_original'),
                        'official_url':row.get('direct_tender_url') or row.get('official_url'),
                        'source_id':row.get('source_id'),
                        'previous_report_route':row.get('route'),
                        'deadline_date_unverified':row.get('deadline_date'),
                        'contacts_unverified':row.get('contact_email'),
                        'raw_report_row':row}
                counter[self._store(rid,bucket,record['source_id'],record['country'],
                                     row,record,utcnow().isoformat(),tier=0)] += 1
        return dict(counter)

    def count(self):
        return self.db.execute('SELECT COUNT(*) FROM leads').fetchone()[0]

    def store_research(self, record_id, name, status, facts, evidence, *, now=None):
        item={'tool':name,'status':status,'facts':facts,'evidence':evidence,
              'model_facts_are_unverified':True}
        key=_sha(item)
        self.db.execute('INSERT OR IGNORE INTO research VALUES (?,?,?,?,?,?)',
                        (record_id,key,(now or utcnow()).isoformat(),name,status,_json(item)))
        self.db.commit()
        return key

    def last_research(self, record_id):
        rows=self.db.execute('SELECT proposal_json FROM research WHERE record_id=? ORDER BY researched_at',
                             (record_id,)).fetchall()
        return [json.loads(x[0]) for x in rows]

    def get(self, record_id):
        row=self.db.execute('SELECT canonical_json FROM leads WHERE record_id=?',(record_id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def inventory(self, *, now: datetime | None = None):
        from agent.portfolio import report_row
        now=now or utcnow()
        items=[]
        for row in self.db.execute('''SELECT record_id,kind,source_id,country,first_seen,last_seen,
                                     raw_json,canonical_json,validation_error FROM leads ORDER BY record_id'''):
            rid,kind,src,country,first,last,raw_text,canon_text,error=row
            raw=json.loads(raw_text) if raw_text else {}
            obj=json.loads(canon_text) if canon_text else {}
            if kind=='tender' and obj:
                item=report_row(obj,raw)
                item['pipeline_bucket']=deadline_bucket(obj,now=now,raw=raw)
                item['deadline_evidence_verified']=_evidence(obj)
                hint=_source_deadline_evidence(raw,now=now)
                item['source_deadline_original']=raw.get('deadline_text')
                item['source_deadline_window_verified']=bool(hint and hint['source_proven'])
                item['central_decision']=obj['assessment']['decision']
            elif kind=='planned_project' and obj:
                item=report_row(obj,raw)
                item['pipeline_bucket']='planned_project'
                item['deadline_evidence_verified']=False
                item['central_decision']=None
            elif kind in ('reported_tender','reported_plan'):
                item={'record_id':rid,'record_type':kind,'country':country,
                    'source_id':src,'title_original':obj.get('title_original'),
                    'official_url':obj.get('official_url'),
                    'direct_tender_url':obj.get('official_url') if kind=='reported_tender' else None,
                    'bid_deadline':None, 'deadline_timezone':None,
                    'previous_report_route':obj.get('previous_report_route'),
                    'contacts':[], 'contact_needed':True,'contact_verified':False,
                    'pipeline_bucket':('planned_project_unverified' if kind=='reported_plan'
                                       else 'reported_only_requires_refresh'),
                    'not_an_open_tender':kind=='reported_plan',
                    'deadline_evidence_verified':False,'central_decision':None,
                    'opportunity_group_id':'source_report:'+rid}
                # Source report contact must not become verified contact. Keep
                # alongside the row for prioritization without changing status.
                if obj.get('contacts_unverified'):
                    item['unverified_report_email']=obj['contacts_unverified']
            else:
                item={'record_id':rid,'record_type':kind,'source_id':src,'country':country,
                      'title_original':raw.get('title_original'),
                      'official_url':raw.get('tender_url') or raw.get('listing_url'),
                      'direct_tender_url':raw.get('tender_url'),
                      'bid_deadline':None,'contacts':[],'contact_needed':True,
                      'pipeline_bucket':'quarantine_requires_review',
                      'deadline_evidence_verified':False,
                      'central_decision':None,'opportunity_group_id':'quarantine:'+rid}
            for key in ('official_url','direct_tender_url','official_source_url'):
                if key in item:item[key]=_public_url(item.get(key))
            for contact in item.get('contacts') or []:
                if isinstance(contact,dict) and 'evidence_url' in contact:
                    contact['evidence_url']=_public_url(contact.get('evidence_url'))
            item.update(first_seen=first,last_seen=last,source_id=src or item.get('source_id'),
                        validation_error=error)
            items.append(item)
        return items

    def snapshot(self, *, now: datetime | None = None):
        rows=self.inventory(now=now)
        by=Counter(r['pipeline_bucket'] for r in rows)
        priority={'future_bid_evidenced':0,'future_bid_source_verified':0,
                  'future_bid_unverified':1,'today_time_unverified':2,
                  'deadline_unknown':3,'reported_only_requires_refresh':4,
                  'planned_project':5,'planned_project_unverified':5,
                  'past_deadline_review':6,'history':7,'excluded':8}
        rows.sort(key=lambda r:(priority.get(r['pipeline_bucket'],9), r.get('bid_deadline') or '9999-12-31',
                                r.get('country') or '',r.get('record_id') or ''))
        return {'generated_at':(now or utcnow()).isoformat(),
                'counts':dict(by),'total':len(rows),
                'actionable_future_bid_count':by['future_bid_evidenced']+by['future_bid_source_verified'],
                'source_native_future_bid_count':by['future_bid_source_verified'],
                'needs_verification_count':sum(by[k] for k in ['future_bid_unverified','today_time_unverified','deadline_unknown','reported_only_requires_refresh']),
                'planned_projects':by['planned_project']+by['planned_project_unverified'],
                'fully_sourced_planned_projects':by['planned_project'],
                'report_only_planned_projects':by['planned_project_unverified'],
                'contactless_retained':sum(not any(c.get('email') for c in r.get('contacts',[])) for r in rows),
                'rows':rows}

    def export(self, directory, *, now: datetime | None = None):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        snapshot=self.snapshot(now=now)
        (directory/'all_candidates.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
        cols=['record_id','record_type','country','source_id','title_original','direct_tender_url',
              'official_url','pipeline_bucket','central_decision','bid_deadline','source_deadline_original',
              'source_deadline_window_verified','deadline_timezone',
              'deadline_evidence_verified','contact_email','contact_name','contact_phone',
              'contact_verified','contact_evidence_url','first_seen','last_seen']
        def write_csv(path,rows):
            with path.open('w',encoding='utf-8-sig',newline='') as fh:
                w=csv.DictWriter(fh,fieldnames=cols);w.writeheader()
                for row in rows:
                    contacts=row.get('contacts') or []
                    c=next((x for x in contacts if x.get('verified') and x.get('email')),
                           next((x for x in contacts if x.get('email')),contacts[0] if contacts else {}))
                    data={k:row.get(k) for k in cols}
                    data.update(contact_email=c.get('email') or row.get('unverified_report_email'),
                                contact_name=c.get('name'),contact_phone=c.get('phone'),
                                contact_verified=bool(c.get('verified')),
                                contact_evidence_url=c.get('evidence_url'))
                    w.writerow(data)
        write_csv(directory/'leads.csv',snapshot['rows'])
        public={k:v for k,v in snapshot.items() if k!='rows'}
        (directory/'summary.json').write_text(json.dumps(public,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        views = {
            'future_deadlines': {'future_bid_evidenced','future_bid_source_verified','future_bid_unverified'},
            'needs_verification': {'today_time_unverified','deadline_unknown','reported_only_requires_refresh','quarantine_requires_review'},
            'future_projects': {'planned_project','planned_project_unverified'},
            'history': {'history','past_deadline_review'},
            'excluded': {'excluded'},
        }
        for name,buckets in views.items():
            data=[r for r in snapshot['rows'] if r['pipeline_bucket'] in buckets]
            (directory/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            write_csv(directory/(name+'.csv'),data)
        return public
