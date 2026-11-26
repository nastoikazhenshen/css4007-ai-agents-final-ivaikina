"""Lossless minimum-field inventory before business screening; not an Active-tender feed."""
import csv,hashlib,json
from urllib.parse import urlsplit
from .relevance import assess
from .runtime import allowed

def remember(state,config,record):
    title=record.get('title_original');url=record.get('tender_url');country=record.get('country_code')
    if not isinstance(title,str) or not title.strip() or country!=config['country_code'] or not url or not allowed(url,config['approved_hosts']):return None
    key=str(record.get('source_tender_id') or 'local_url_'+hashlib.sha256(url.encode()).hexdigest())
    scope=assess(title,record.get('description_original'))
    classification='unclassified' if scope['reason'].startswith('No contextual evidence') else scope['verdict']
    row=dict(country=country,title=title,official_url=url,source_id=config['source_id'],source_tender_id=record.get('source_tender_id'),buyer=record.get('buyer_original'),published_on=record.get('published_on'),observed_at=record['fetched_at'],relevance=classification,relevance_reason=scope['reason'],not_an_active_claim=True)
    with state.db:
        state.db.execute('CREATE TABLE IF NOT EXISTS discovery_notices(source TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(source,record_key))')
        state.db.execute('INSERT OR REPLACE INTO discovery_notices VALUES (?,?,?)',(config['source_id'],key,json.dumps(row,ensure_ascii=False)))
    return key

def export(state,source,keys,path):
    state.db.execute('CREATE TABLE IF NOT EXISTS discovery_notices(source TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(source,record_key))')
    rows=[json.loads(payload) for key,payload in state.db.execute('SELECT record_key,payload FROM discovery_notices WHERE source=? ORDER BY record_key',(source,)) if key in keys]
    fields=['country','title','official_url','source_id','source_tender_id','buyer','published_on','observed_at','relevance','relevance_reason','not_an_active_claim']
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for row in rows:w.writerow({k:"'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for k,v in row.items()})
    return rows
