"""Lossless tender + planned-project reporting without turning leads into Active.

Cross-source identity groups preserve *all* raw cards/provenance. Source IDs may
refer to one economic opportunity (PNCP and Compras), so do not sum raw rows as
unique commercial opportunities. No schema migrations and no Sheets writes.
"""
from __future__ import annotations
import hashlib
import re
from collections import defaultdict
from urllib.parse import urlsplit


def evidence_category(tender):
    if tender.get('record_type')=='planned_project_proposal':return 'planned_projects'
    return (tender.get('assessment') or {}).get('decision') or 'pending_verification'


def report_row(tender, raw_notice=None):
    """A final monitor *lead*, including contactless tenders and future projects."""
    raw_notice=raw_notice or {}
    planned=tender.get('record_type')=='planned_project_proposal'
    project=tender.get('project') or {}
    source=tender.get('source') or {}
    procurement=tender.get('procurement') or {}
    contact_rows=tender.get('contacts') or []
    if planned:
        title=tender.get('title_original')
        country=tender.get('country_code')
        official=tender.get('official_plan_url')
        direct=None
        landing=official
        contact_rows=tender.get('contact_hints') or []
    else:
        title=project.get('title')
        country=project.get('country_code')
        direct=source.get('tender_url')
        landing=source.get('source_url')
        official=direct or landing
    identity=(raw_notice.get('raw_payload') or {}).get('canonical_identity') or {}
    # Identity grouping only when a platform-issued canonical ID has clear namespace.
    if (identity.get('namespace') and identity.get('value')):
        group_id='canonical:'+str(identity['namespace'])+':'+str(identity['value'])
    else:
        group_id='source:'+str(source.get('source_id') or tender.get('source_id'))+':'+str(
            raw_notice.get('source_tender_id') or tender.get('record_id') or tender.get('project_key'))
    contacts=[]
    for c in contact_rows:
        if not isinstance(c,dict):continue
        if c.get('email') or c.get('name') or c.get('phone'):
            contacts.append({'email':c.get('email'),'name':c.get('name'),
              'phone':c.get('phone'),'verified':c.get('verified') is True,
              'evidence_url':c.get('evidence_url') or c.get('source_url')})
    return {'record_id':tender.get('record_id') or tender.get('project_key'),
        'opportunity_group_id':group_id,'record_type':'planned_project' if planned else 'tender',
        'country':country,'title_original':title,'official_url':official,
        'direct_tender_url':direct,'official_source_url':landing,
        'source_id':source.get('source_id') or tender.get('source_id'),
        'route':evidence_category(tender),
        'bid_deadline':None if planned else procurement.get('deadline_date'),
        'deadline_timezone':None if planned else procurement.get('deadline_timezone'),
        'contacts':contacts,
        'contact_needed':not any(c.get('email') for c in contacts),
        'contact_verified':any(c.get('email') and c.get('verified') for c in contacts),
        'docs_count':len(tender.get('documents') or []),
        'needs_direct_url':not bool(direct) if not planned else False,
        'missing_identity':not bool(title and country and official),
        'not_an_open_tender':planned,
        'no_contact_does_not_delete':True,
    }


def summarize(rows):
    by_route=defaultdict(int);by_country=defaultdict(int);grouped=defaultdict(list)
    for row in rows:
        by_route[row['route']]+=1
        by_country[row['country']]+=1
        grouped[row['opportunity_group_id']].append(row['record_id'])
    return {'candidate_rows':len(rows),'opportunity_identity_groups':len(grouped),
        'cross_source_groups':{k:v for k,v in grouped.items() if len(v)>1},
        'routes':dict(by_route),'countries':dict(by_country),
        'contactless_preserved':sum(r['contact_needed'] for r in rows),
        'with_email':sum(not r['contact_needed'] for r in rows),
        'with_verified_email':sum(r['contact_verified'] for r in rows),
        'with_direct_tender_url':sum(bool(r['direct_tender_url']) for r in rows),
        'with_official_url':sum(bool(r['official_url']) for r in rows),
        'with_deadline_but_no_email':sum(bool(r['bid_deadline']) and r['contact_needed'] for r in rows),
        'planned_projects':sum(r['record_type']=='planned_project' for r in rows),
        'records_not_deleted':True,
    }
