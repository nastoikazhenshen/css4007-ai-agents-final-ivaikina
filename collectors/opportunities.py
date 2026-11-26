"""Active-first procurement collection. Archive volume and central routing are separate metrics."""
from __future__ import annotations
import argparse,collections,importlib,json,uuid
from pathlib import Path
from datetime import datetime,timezone,date,timedelta
from urllib.parse import urlencode,urlsplit,parse_qs
import httpx
from collectors.common.config import load
from collectors.common.runtime import CollectorState,Fetcher,CurlTransport,SourceError,utc,fingerprint,schema
from collectors.common.pagination import Pager
from collectors.common.records import candidate,pdf_contacts
from collectors.common.opportunity import decide,instant_bounds
from collectors.common.relevance import assess
from collectors.common.discovery import remember,export as export_discovery
from integration.collector_validation import validate_run
from integration.candidate_store import CandidateInbox
from integration.pipeline import process_run
MODULES={'br_compras':'collectors.brazil.compras','br_pncp':'collectors.brazil.pncp','us_nypa':'collectors.usa.nypa','us_okanogan':'collectors.usa.okanogan','tr_tredas':'collectors.turkey.tredas','ar_contratar':'collectors.argentina.contratar','th_mea':'collectors.thailand.mea','mx_sidof':'collectors.mexico.sidof'}

def execute(source,options,*,client=None,state=None):
    cfg=load(source);out=Path(options.get('output','var/opportunities'));out.mkdir(parents=True,exist_ok=True);own=state is None;state=state or CollectorState(out/'state');started=utc();own_client=client is None;client=client or httpx.Client(transport=CurlTransport() if options.get('transport')=='curl' else None,trust_env=options.get('transport')!='curl',timeout=45,follow_redirects=False)
    fetch=Fetcher(cfg,state,client,interval=options.get('request_interval',0.3));adapter=importlib.import_module(MODULES[source]);records={};listing_records={};scopes=[];errors=[];discovery_keys=set()
    retained=0;source_pause=None
    if options.get('mode')=='incremental':
        for (payload,) in state.db.execute('SELECT payload FROM collector_records WHERE source=? AND kind=?',(source,'tender')):
            r=json.loads(payload);prior=decide(r,source)
            if prior['state'] not in {'closed','closed_or_unavailable','excluded'}:
                records[r['source_tender_id']]=r;retained+=1
    if source=='br_compras':
        partitions=[(m,q) for m in (options.get('modalities') or [3,5]) for q in options.get('terms',['linha','rede','isolador','condutor','poste'])]
    elif source=='br_pncp':partitions=options.get('modalities') or [4,6]
    elif hasattr(adapter,'partitions'):partitions=list(adapter.partitions(options))
    else:partitions=[None]
    for partition in partitions:
        pager=None
        try:
            if source=='br_compras':
                modality,q=partition;initial=adapter.listing(options,modality)+'&'+urlencode(dict(objetoCompra=q))
            elif source=='br_pncp':initial=adapter.listing(dict(options,open_proposals=options.get('mode')!='incremental',page_size=50),partition)
            else:initial=adapter.listing(options,partition)
            scope=source+':active_v2:'+fingerprint(initial)
            pager=Pager(fetch,scope,initial,adapter.page,lambda r:str(r.get('numeroControlePNCP') or r.get('rfqid') or r.get('_source_key') or r.get('id') or ''),max_pages=options.get('max_pages'),resume=options.get('resume',False),loader=(lambda url:adapter.loader(fetch,url)) if hasattr(adapter,'loader') else None).collect()
            scopes.append(dict(partition=partition,pages=pager.pages,requests=pager.requests,total=pager.total,received=len(pager.rows),complete=pager.complete,next_url=pager.next_url))
            if pager.error:
                errors.append(dict(error_code=pager.error.code,message=str(pager.error),retryable=pager.error.retryable))
                if pager.error.code in {'http_403','captcha','auth_required','http_429','http_503','timeout'}:source_pause=pager.error
            for row,observed in pager.rows:
                try:
                    original=row;row=adapter.normalize(row) if source=='br_compras' else row;r=adapter.parse(cfg,row,observed)
                    r['listing_url']=initial;r['raw_payload']['original_received_record']=original
                    listing_records[r['source_tender_id']]=r
                    discovery_key=remember(state,cfg,r)
                    if discovery_key:discovery_keys.add(discovery_key)
                    if source in {'us_nypa','ar_contratar'} or candidate(r.get('title_original'),r.get('description_original')) or hasattr(adapter,'must_inspect') and adapter.must_inspect(row):records[r['source_tender_id']]=r
                except (SourceError,KeyError,ValueError,TypeError) as e:errors.append(dict(error_code='parse_error',message=str(e),retryable=False))
            if source_pause:break
        except SourceError as e:
            errors.append(dict(error_code=e.code,message=str(e),retryable=e.retryable))
            scopes.append(dict(partition=partition,pages=0,requests=0,total=None,received=0,complete=False,next_url=None))
            if e.code in {'http_403','captcha','auth_required','http_429','http_503','timeout'}:source_pause=e;break
    # Item inspection can rescue generic headlines before the topic gate. Missing secondary
    # fields never remove a notice from the minimum-field inventory or the raw page store.
    item_report=dict(eligible=0,attempted=0,complete=0,received=0,recovered_candidates=0,
                     page_budget=options.get('item_max_pages'),stop_reason=None)
    if source in {'br_compras','br_pncp'} and not source_pause and options.get('item_limit',0):
        from collectors.brazil.items import collect as collect_items
        queue=[]
        for r in listing_records.values():
            verdict=assess(r.get('title_original'),r.get('description_original'))
            end=instant_bounds(r.get('deadline_text'))
            if end and end[1]<=datetime.now(timezone.utc):continue
            if verdict['verdict']=='excluded' and not verdict['reason'].startswith('No contextual evidence'):continue
            key=source+':items_inspected:'+str(r['source_tender_id'])
            prior=state.checkpoint(key) or {};signature=fingerprint(r['raw_payload']['source_record'])
            queue.append((prior.get('signature')==signature,0 if verdict['verdict']=='needs_documents' else 1 if verdict['verdict']=='excluded' else 2,prior.get('checked_at',''),key,signature,r))
        queue.sort(key=lambda x:x[:4]);item_report['eligible']=len(queue)
        limit=options['item_limit'];selected=queue if limit<0 else queue[:limit]
        for _,_,_,key,signature,r in selected:
            result=collect_items(fetch,r,max_pages=options.get('item_max_pages'),
                                 resume=options.get('resume',False))
            item_report['attempted']+=1;item_report['complete']+=result['complete'];item_report['received']+=result['received']
            state.checkpoint(key,dict(signature=signature,checked_at=result['checked_at'],complete=result['complete'],source_url=result['source_url']))
            if candidate(r.get('title_original'),r.get('description_original')):
                if r['source_tender_id'] not in records:item_report['recovered_candidates']+=1
                records[r['source_tender_id']]=r
            discovery_key=remember(state,cfg,r)
            if discovery_key:discovery_keys.add(discovery_key)
            if result['error'] and result['error']['code'] in {'http_403','captcha','auth_required','http_429','http_503','timeout'}:
                item_report['stop_reason']=result['error']['code'];break
    for r in records.values():
        bounds=instant_bounds(r.get('deadline_text'))
        if bounds and bounds[1]<=datetime.now(timezone.utc):continue
        if hasattr(adapter,'needs_enrichment') and not adapter.needs_enrichment(r):continue
        # A failed new detail request cannot silently reuse an old observation as fresh proof.
        r['raw_payload'].pop('verified_detail',None);r['raw_payload'].pop('detail_checked_at',None);r['raw_payload'].pop('detail_error',None)
        if source_pause:
            r['raw_payload']['detail_error']=dict(code=source_pause.code,reason='Source paused after listing restriction/retry limit');continue
        try:
            if source in {'br_compras','br_pncp'}:
                from collectors.brazil.active import enrich
                enrich(fetch,r)
            elif hasattr(adapter,'enrich'):adapter.enrich(fetch,r)
        except SourceError as e:r['raw_payload']['detail_error']=dict(code=e.code,reason=str(e))
        r['raw_payload']['verification']=decide(r,source)
    # Give uncertain scopes the first document, then share the remaining budget round-robin.
    # A strong headline must not consume all documents before ambiguous projects are inspected.
    budget=0 if source_pause else options.get('document_limit',0);used=0
    ordered=sorted(records.values(),key=lambda r:0 if assess(r.get('title_original'),r.get('description_original'))['verdict']=='needs_documents' else 1)
    jobs=[];enumerated=0;document_listing_limit=0 if source_pause else options.get('document_list_limit',budget)
    for r in ordered:
        proof=r['raw_payload'].get('verification') or decide(r,source);r['raw_payload']['verification']=proof
        if proof['state'] in {'excluded','closed','closed_or_unavailable'}:continue
        try:
            if source in {'br_compras','br_pncp'} and (document_listing_limit<0 or enumerated<document_listing_limit):
                from collectors.brazil.pncp import documents
                enumerated+=1;r['documents']=documents(fetch,r);r['raw_payload']['document_listing_checked_at']=utc()
            # Prefer procurement terms/specifications, not supplier certificates or correspondence.
            docs=sorted(r['documents'],key=lambda d:0 if any(s in (d.get('title') or '').casefold() for s in ['edital','refer','memorial','projeto','especific','terms']) else 1)
            if docs:jobs.append((r,docs))
        except SourceError as e:r['raw_payload']['document_error']=dict(code=e.code,reason=str(e))
    rounds=max((len(docs) for _,docs in jobs),default=0)
    for index in range(rounds):
        for r,docs in jobs:
            if used>=budget:break
            if index>=len(docs):continue
            doc=docs[index];proof=r['raw_payload'].get('verification') or decide(r,source)
            try:
                if used>=budget:break
                downloaded=fetch.attachment(source,r['source_tender_id'],doc,out);r['raw_payload']['documents'].append(downloaded);used+=1
                doc['access']='public' if downloaded['download_status']=='downloaded' else 'restricted' if downloaded['download_status']=='access_restricted' else 'unavailable'
                if downloaded.get('local_path'):
                    from collectors.common.document_text import extract
                    extraction=extract(downloaded['local_path']);downloaded['text_extraction']=dict(parts=[{k:v for k,v in part.items() if k!='text'} for part in extraction['parts']],issues=extraction['issues'],complete=extraction['complete'])
                    for part in extraction['parts']:
                        content=part['text'];state.save_extracted(part['sha256'],part['needs_ocr'],content,part['locator'])
                        hint_start=len(r['raw_payload'].get('document_contact_candidates',[]));pdf_contacts(r,content,doc['url'])
                        for hint in r['raw_payload'].get('document_contact_candidates',[])[hint_start:]:hint['locator']=part['locator']+' email occurrence'
                        from collectors.common.contact_attribution import procurement_pdf_contacts
                        procurement_pdf_contacts(r,content,doc['url'],locator=part['locator']+' explicitly designates clarification/challenge email')
                        doc_scope=assess(r['title_original'],content)
                        identity_ok=source!='ar_contratar' or str(r['source_tender_id']) in content
                        if identity_ok and doc_scope['verdict']=='relevant' and (proof['scope']['verdict']=='needs_documents' or doc_scope['overhead_or_product_explicit'] and not proof['scope']['overhead_or_product_explicit']):
                            r['raw_payload']['specification_scope_evidence']=dict(url=doc['url'],sha256=part['sha256'],parent_sha256=downloaded['sha256'],locator=part['locator'],verdict=doc_scope)
                            r['description_original']=(r.get('description_original') or '')+'\n'+content[:200000]
                            proof=decide(r,source)
                r['raw_payload']['verification']=decide(r,source)
            except SourceError as e:r['raw_payload']['document_error']=dict(code=e.code,reason=str(e))
    tenders=list(records.values())
    for r in tenders:
        state.hydrate_documents(source,r)
        if r.get('deadline_text'):r['raw_payload']['missing_fields']=[v for v in r['raw_payload'].get('missing_fields',[]) if v!='bid deadline not confirmed']
        if 'verification' not in r['raw_payload']:r['raw_payload']['verification']=decide(r,source)
        discovery_key=remember(state,cfg,r)
        if discovery_key:discovery_keys.add(discovery_key)
    counts=collections.Counter(r['raw_payload']['verification']['state'] for r in tenders);complete=bool(scopes) and all(s['complete'] for s in scopes) and not errors
    run=dict(schema_version='1.0.0',run_id='active-'+uuid.uuid4().hex,source_id=source,country_code=cfg['country_code'],started_at=started,finished_at=utc(),run_status='success' if complete and tenders else 'empty_unverified' if complete else 'partial' if tenders else 'blocked' if errors and errors[0]['error_code'] in {'http_403','captcha','auth_required'} else 'unavailable',coverage=dict(verified_complete=complete,reason='Declared live query scope only; not full country coverage',date_from=None if source=='br_pncp' else options.get('start'),date_to=options.get('end'),next_cursor=json.dumps([s['next_url'] for s in scopes if s['next_url']]) if any(s['next_url'] for s in scopes) else None),metrics=dict(pages_requested=sum(s['requests'] for s in scopes),pages_received=sum(s['pages'] for s in scopes),records_seen=sum(s['received'] for s in scopes),records_emitted=len(tenders),errors_count=len(errors)),http_observations=fetch.observations,errors=errors,tenders=tenders)
    schema(run,'collector_run.schema.json');validate_run(run);changes=state.merge_records(source,tenders);state.source_run(source,started,run)
    inbox=CandidateInbox(out/'central_inbox.sqlite');central_error=None
    try:central=process_run(run,inbox=inbox)
    except Exception as e:central=[];central_error=str(e)
    finally:inbox.close()
    report=dict(source_id=source,country=cfg['country_code'],checked_at=utc(),rows_received=run['metrics']['records_seen'],state_candidates_rechecked=retained,unique_candidates=len(tenders),outcomes=dict(counts),scopes=scopes,errors=errors,detail_failures=sum(bool(r['raw_payload'].get('detail_error')) for r in tenders),schema_validation='passed',central_records=len(central),central_error=central_error,changes=changes,sqlite_integrity=state.db.execute('PRAGMA integrity_check').fetchone()[0],country_coverage='partial; do not interpret zero matched rows as national absence')
    report['item_enrichment']=item_report
    report['document_enrichment']=dict(listings_attempted=enumerated,listing_budget=document_listing_limit,
        download_budget=budget,downloads_attempted=used,
        with_document_links=sum(bool(r['documents']) for r in tenders),
        files_downloaded=sum(d.get('download_status')=='downloaded' for r in tenders for d in r['raw_payload'].get('documents',[])),
        extraction_issues=sum(len(d.get('text_extraction',{}).get('issues',[])) for r in tenders for d in r['raw_payload'].get('documents',[])))
    dest=out/source;dest.mkdir(exist_ok=True)
    inventory=export_discovery(state,source,discovery_keys,dest/'discovery_inventory.csv');report['minimum_field_notices_saved']=len(inventory);report['discovery_inventory_scope']='All parsed notices with title, country and approved official record URL in this capture; includes irrelevant/archived rows, not an Active count'
    for name,data in [('collector_run.json',run),('report.json',report),('open_verified.json',[r for r in tenders if r['raw_payload']['verification']['state']=='open_verified'])]:(dest/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    if own:state.close()
    if own_client:client.close()
    return run,report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',choices=list(MODULES),required=True);p.add_argument('--start');p.add_argument('--end');p.add_argument('--mode',choices=['full','incremental'],default='full');p.add_argument('--terms',nargs='+',default=['linha','rede','isolador','condutor','poste']);p.add_argument('--modalities',nargs='+',type=int);p.add_argument('--page-size',type=int,default=500);p.add_argument('--max-pages',type=int);p.add_argument('--resume',action='store_true');p.add_argument('--request-interval',type=float,default=1.0,help='Minimum seconds between source requests');p.add_argument('--item-limit',type=int,default=25,help='PNCP item inspection purchases: -1 all, 0 off');p.add_argument('--item-max-pages',type=int);p.add_argument('--document-list-limit',type=int,default=50,help='Attachment metadata purchases: -1 all, 0 off');p.add_argument('--document-limit',type=int,default=10);p.add_argument('--output',default='var/opportunities');p.add_argument('--transport',choices=['curl','httpx'],default='httpx');a=vars(p.parse_args());
    if not 0.1<=a['request_interval']<=60:p.error('request-interval must be 0.1..60 seconds')
    if a['item_limit'] < -1 or a['document_list_limit'] < -1:p.error('enrichment limits must be -1, 0 or positive')
    if a['item_max_pages'] is not None and a['item_max_pages']<1:p.error('item-max-pages must be positive')
    source=a.pop('source');a['start']=a['start'] or (date.today()-timedelta(days=7 if a['mode']=='incremental' else 365)).isoformat();a['end']=a['end'] or (date.today()+timedelta(days=365) if source=='br_pncp' and a['mode']=='full' else date.today()).isoformat();_,r=execute(source,a);print(json.dumps(r,ensure_ascii=False))
if __name__=='__main__':main()
