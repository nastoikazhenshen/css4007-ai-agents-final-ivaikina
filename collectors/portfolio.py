"""Private operational view of collector proofs; not a replacement central schema/Active decision."""
from __future__ import annotations
import argparse,collections,csv,hashlib,json,re
from pathlib import Path
from collectors.common.runtime import fingerprint,schema,utc
from collectors.common.relevance import norm
from collectors.common.opportunity import decide

def identity(source,record):
    official=record['raw_payload'].get('canonical_identity')
    if official and official.get('namespace')=='pncp':return 'pncp:'+official['value']
    return source+':'+str(record['source_tender_id'])

def potential_duplicate(record):
    """Same scope/buyer/amount/day is a review group, not proof two official IDs are identical."""
    detail=record['raw_payload'].get('verified_detail') or {};buyer=detail.get('orgaoEntidade') or {}
    if not buyer.get('cnpj') or detail.get('valorTotalEstimado') is None:return None
    title=re.sub(r'^\[[^]]+\]\s*-?\s*','',norm(record.get('title_original')))
    title=re.sub(r'[^a-z0-9]','',title)
    return fingerprint([buyer['cnpj'],title,detail['valorTotalEstimado'],record.get('deadline_text')])

def build(runs,*,now=None):
    identities={};reports=[]
    for run in runs:
        schema(run,'collector_run.schema.json');source=run['source_id'];counts=collections.Counter()
        for record in run['tenders']:
            proof=decide(record,source,now=now);counts[proof['state']]+=1
            key=identity(source,record)
            item=identities.setdefault(key,dict(key=key,source_aliases=[],observations=[]))
            item['source_aliases'].append(dict(source=source,id=record['source_tender_id'],url=record['tender_url']))
            item['observations'].append(dict(source=source,record=record,proof=proof))
        reports.append(dict(source=source,received=run['metrics']['records_seen'],emitted=len(run['tenders']),scope_complete=run['coverage']['verified_complete'],outcomes=dict(counts)))
    entries=[]
    for item in identities.values():
        # Fresh proof wins; documents and contact evidence stay alongside every source observation.
        observations=item['observations'];best=max(observations,key=lambda o:(o['proof'].get('checked_at') or '',o['record']['fetched_at']))
        # If current observations disagree, never hide a cancelled/closed source result.
        states={o['proof']['state'] for o in observations}
        if 'open_verified' in states and states&{'closed','closed_or_unavailable'}:
            state='pending_verification';reason='Conflicting source observations; source identity retained for review'
        else:state=best['proof']['state'];reason=best['proof']['reason']
        record=best['record'];contacts=[];documents=[]
        for o in observations:
            contacts.extend(o['record']['contact_hints']);documents.extend(o['record']['documents'])
        contacts=list({json.dumps(c,sort_keys=True):c for c in contacts}.values());documents=list({d['url']:d for d in documents}.values())
        raw=record['raw_payload'];detail=raw.get('verified_detail') or {}
        title=norm(record.get('title_original'));mixed=bool(re.search(r'abastecimento de agua|infraestrutura hidrica|sistema de bombeamento|pocos? tubulares?',title))
        value=detail.get('valorTotalEstimado');priority='secondary_embedded_electrical_scope' if mixed else 'lower_priority_local_scope' if re.search(r'\b180\s*(?:metros|m)\b',title) else 'large_total_budget_signal_not_product_quantity' if record['country_code']=='BR' and isinstance(value,(int,float)) and value>=1_000_000 else 'scale_requires_review'
        entries.append(dict(key=item['key'],country=record['country_code'],company=record['buyer_original'],project=record['title_original'],state=state,reason=reason,deadline=record['deadline_text'],official_url=raw.get('portal_url') or record['tender_url'],contacts=contacts,documents=documents,source_aliases=item['source_aliases'],potential_duplicate_group=potential_duplicate(record),estimated_value=value,commercial_priority=priority,scope_relation='embedded_electrical_section_of_other_infrastructure' if mixed else 'primary_scope_requires_normal_review',overhead_or_product_explicit=best['proof']['scope'].get('overhead_or_product_explicit',False),source_proof=best['proof'],observations=observations))
    open_rows=[e for e in entries if e['state']=='open_verified'];groups={e['potential_duplicate_group'] or e['key'] for e in open_rows}
    return dict(generated_at=utc(),notice='Collector-side current window proof, not central Active routing. Potential duplicate groups require review; no official IDs or aliases deleted.',summary=dict(sources=reports,retained_identities=len(entries),open_official_identities=len(open_rows),possible_distinct_open_groups=len(groups),open_countries=sorted({e['country'] for e in open_rows}),open_with_contact=sum(bool(e['contacts']) for e in open_rows),open_with_email=sum(any(c.get('email') for c in e['contacts']) for e in open_rows),open_with_document_link=sum(bool(e['documents']) for e in open_rows),open_with_explicit_overhead_or_product=sum(e['overhead_or_product_explicit'] for e in open_rows)),entries=entries)

def write(portfolio,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    (directory/'portfolio.json').write_text(json.dumps(portfolio,ensure_ascii=False,indent=2)+'\n')
    (directory/'summary.json').write_text(json.dumps(portfolio['summary'],ensure_ascii=False,indent=2)+'\n')
    fields=['Country','Company','Project','Contact Person','Email','Phone','Tender Link','Deadline','Specification Link','Source State','Reason','Explicit OHL or Product','Estimated Value','Source Aliases','Possible Duplicate Group','Commercial Priority','Scope Relation','Publication Date','Original Status','Last Source Check','Proof URL','Deadline Basis','Timezone Method']
    for filename,states in [('Open Source Windows.csv',{'open_verified'}),('Pending Verification.csv',{'pending_verification'}),('Tender History.csv',{'closed','closed_or_unavailable'}),('Excluded.csv',{'excluded'})]:
        with (directory/filename).open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.writer(f);w.writerow(fields)
            for e in portfolio['entries']:
                if e['state'] not in states:continue
                values=[e['country'],e['company'],e['project'],'; '.join(c['name'] for c in e['contacts'] if c.get('name')),'; '.join(c['email'] for c in e['contacts'] if c.get('email')),'; '.join(c['phone'] for c in e['contacts'] if c.get('phone')),e['official_url'],e['deadline'],'; '.join(d['url'] for d in e['documents']),e['state'],e['reason'],e['overhead_or_product_explicit'],e['estimated_value'],json.dumps(e['source_aliases'],ensure_ascii=False),e['potential_duplicate_group'],e['commercial_priority'],e['scope_relation']]
                proof=e['source_proof'];observed=e['observations'][0]['record']
                values.extend([observed.get('published_on'),proof.get('source_status'),proof.get('checked_at'),proof.get('proof_url'),proof.get('deadline_basis') or observed['raw_payload'].get('deadline_basis'),proof.get('timezone_method')])
                w.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@','\t','\r')) else v for v in values])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',nargs='+',required=True);p.add_argument('--output',default='var/portfolio');a=p.parse_args()
    portfolio=build([json.loads(Path(f).read_text()) for f in a.runs]);write(portfolio,a.output);print(json.dumps(portfolio['summary'],ensure_ascii=False))
if __name__=='__main__':main()
