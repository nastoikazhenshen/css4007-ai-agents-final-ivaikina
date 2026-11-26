"""Read-only API collectors. Run with python -m collectors.run --help."""
from __future__ import annotations
import argparse,importlib,json,uuid
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import httpx
from collectors.common.config import load
from collectors.common.runtime import CollectorState,Fetcher,CurlTransport,SourceError,schema,utc,fingerprint
from collectors.common.pagination import Pager
from collectors.common.records import candidate,pdf_contacts
from collectors.common.discovery import remember, export as export_discovery
from integration.collector_validation import validate_run
from integration.candidate_store import CandidateInbox
from integration.pipeline import process_run

ADAPTERS={'br_pncp':'collectors.brazil.pncp','br_compras':'collectors.brazil.compras','pe_oece':'collectors.peru.oece','us_sam':'collectors.usa.sam','ar_energia_tenders':'collectors.argentina.energia_tenders'}
def months(start,end):
    current=date.fromisoformat(start).replace(day=1);stop=date.fromisoformat(end)
    while current<=stop:
        yield current.strftime('%Y-%m');current=(current.replace(day=28)+timedelta(days=4)).replace(day=1)
def execute(source,options,*,client=None,state=None,sleep=None):
    config=load(source);output=Path(options.get('output','var/collectors'));output.mkdir(parents=True,exist_ok=True)
    own=state is None;state=state or CollectorState(output/'state');started=utc();errors=[];records={};scopes=[];rejected=[];discovery_keys=set()
    adapter=importlib.import_module('collectors.common.world_bank' if source.endswith('_world_bank') else ADAPTERS[source])
    transport=CurlTransport() if options.get('transport')=='curl' else None
    ownclient=client is None;client=client or httpx.Client(transport=transport,trust_env=transport is None,timeout=45,follow_redirects=False,headers={'User-Agent':'public_research-TenderScout/0.1 (official-public-data; bounded read-only requests)'})
    fetch=Fetcher(config,state,client=client,**({'sleep':sleep,'interval':0} if sleep else {'interval':options.get('request_interval',0.3)}))
    partitions=options.get('modalities',[6,8]) if source in {'br_pncp','br_compras'} else list(months(options['start'],options['end'])) if source=='pe_oece' else [adapter.COUNTRIES[config['country_code']]] if source.endswith('_world_bank') else [e['url'] for e in config['endpoints'] if e['purpose']=='detail'] if config['connector_type']=='public_html' else [None]
    for partition in partitions:
        pager=None
        try:
            initial=adapter.listing(options,partition)
            scope=source+':'+fingerprint({'partition':partition,'start':options['start'],'end':options['end'],'mode':options.get('mode'),'page_size':options.get('page_size'),'query':options.get('query'),'category':options.get('category'),'oece_pagination':options.get('oece_pagination'),'oece_source':options.get('oece_source'),'open_proposals':options.get('open_proposals')})
            ident=lambda r:str(r.get('numeroControlePNCP') or r.get('ocid') or r.get('noticeId') or r.get('id') or '')
            loader=None
            if config['connector_type']=='public_html':
                def loader(url):
                    ok,reason=fetch.public_file_allowed(url)
                    if not ok:raise SourceError(reason,'auth_required')
                    return fetch.get(url).text
            pager=Pager(fetch,scope,initial,adapter.page,ident,loader=loader,max_pages=options.get('max_pages'),resume=options.get('resume',False),allow_overlap=source=='pe_oece').collect()
            if pager.error:errors.append(dict(error_code=pager.error.code,message=str(pager.error),retryable=pager.error.retryable))
            for row,observed in pager.rows:
                original=row
                try:
                    if source=='br_compras':row=adapter.normalize(row)
                    record=adapter.parse(config,row,observed)
                    discovery_key=remember(state,config,record)
                    if discovery_key:discovery_keys.add(discovery_key)
                    # Every raw row has already been persisted by Pager. No early category/voltage/email gates.
                    if options.get('retain_all') or candidate(record['title_original'],record['description_original']):
                        if source=='br_compras':record['raw_payload']['original_compras_record']=original
                        records[record['source_tender_id']]=record
                except (KeyError,ValueError,TypeError,SourceError) as e:
                    rejected.append(dict(id=ident(original),reason=str(e)))
            scopes.append(dict(partition=partition,pages=pager.pages,requests=pager.requests,total=pager.total,received=len(pager.rows),raw_rows_received=pager.received_rows,overlap_rows=pager.overlap_rows,complete=pager.complete,next_url=__import__('collectors.common.runtime',fromlist=['safe_url']).safe_url(pager.next_url) if pager.next_url else None,scope=scope))
            if pager.error and pager.error.code in {'http_403','captcha','auth_required','http_429','http_503','timeout'}:break
        except SourceError as e:
            errors.append(dict(error_code=e.code,message=str(e),retryable=e.retryable));scopes.append(dict(partition=partition,pages=0,requests=0,total=None,received=0,complete=False,next_url=None))
            if e.code in {'http_403','captcha','auth_required','http_429','http_503','timeout'}:break
    if rejected:errors.append(dict(error_code='parse_error',message=f'{len(rejected)} rows could not be normalized; raw rows preserved in SQLite',retryable=False))
    # Attachment enumeration/download is bounded separately and never drops a notice.
    document_budget=options.get('document_limit',0);attempted=0
    for record in records.values():
        if attempted>=document_budget:break
        if source in {'br_pncp','br_compras'}:
            from collectors.brazil.pncp import documents
            try:
                record['documents']=documents(fetch,record);record['raw_payload']['document_listing_checked_at']=utc()
            except SourceError as e:record['raw_payload']['document_lookup_error']=str(e)
        for doc in record['documents']:
            if attempted>=document_budget:break
            result=fetch.attachment(source,record['source_tender_id'],doc,output);attempted+=1
            record['raw_payload']['documents'].append(result)
            doc['access']='public' if result['download_status']=='downloaded' else 'restricted' if result['download_status']=='access_restricted' else 'unavailable'
            if result.get('local_path','').endswith('.pdf'):
                try:
                    import fitz
                    with fitz.open(result['local_path']) as pdf:
                        content='\n'.join(page.get_text() for page in pdf);page_count=len(pdf)
                    state.save_extracted(result['sha256'],False,content,'PDF pages 1-'+str(page_count))
                    pdf_contacts(record,content,doc['url'])
                except Exception as e:result['extraction_error']=type(e).__name__
    for record in records.values():state.hydrate_documents(source,record)
    complete=bool(scopes) and all(s['complete'] for s in scopes) and not rejected
    tenders=list(records.values());status='success' if complete and tenders else 'empty_unverified' if complete else 'partial' if tenders or any(s['received'] for s in scopes) else 'blocked' if errors and errors[0]['error_code'] in {'auth_required','http_403','captcha'} else 'unavailable'
    run=dict(schema_version='1.0.0',run_id=source+'-'+uuid.uuid4().hex,source_id=source,country_code=config['country_code'],started_at=started,finished_at=utc(),run_status=status,
             coverage=dict(verified_complete=complete,reason='Complete for declared partitions and filters; not all national procurement' if complete else 'Partial/unverified scope: see coverage detail and errors',date_from=None if source.endswith('_world_bank') or options.get('open_proposals') else options['start'],date_to=None if source.endswith('_world_bank') else options['end'],next_cursor=json.dumps([s['next_url'] for s in scopes if s['next_url']]) if any(s['next_url'] for s in scopes) else None),
             metrics=dict(pages_requested=sum(s['requests'] for s in scopes),pages_received=sum(s['pages'] for s in scopes),records_seen=sum(s['received'] for s in scopes),records_emitted=len(tenders),errors_count=len(errors)),http_observations=fetch.observations,errors=errors,tenders=tenders)
    schema(run,'collector_run.schema.json');validate_run(run)
    delta=state.merge_records(source,tenders);state.source_run(source,started,run)
    # Existing central path, using an independent inbox. Does not call remote Sheets/AI.
    inbox=CandidateInbox(output/'central_inbox.sqlite')
    central_errors=[];processed=[]
    try:
        processed=process_run(run,inbox=inbox,now=datetime.now(timezone.utc))
    except Exception as e:central_errors.append(type(e).__name__+': '+str(e))
    finally:inbox.close()
    summary=dict(source_id=source,checked_at=utc(),scope=options,coverage=scopes,records_seen=run['metrics']['records_seen'],candidates=len(tenders),with_official_record_url=sum(bool(r['tender_url']) for r in tenders),with_contact=sum(bool(r['contact_hints']) for r in tenders),with_email=sum(any(c.get('email') for c in r['contact_hints']) for r in tenders),with_unattributed_document_emails=sum(bool(r['raw_payload'].get('document_contact_candidates')) for r in tenders),with_document_link=sum(bool(r['documents']) for r in tenders),documents_downloaded=sum(d.get('download_status')=='downloaded' for r in tenders for d in r['raw_payload']['documents']),schema_validation='passed',central_records=len(processed),central_errors=central_errors,changes=delta,sqlite_integrity=state.db.execute('PRAGMA integrity_check').fetchone()[0],rejected_rows=rejected,run_status=status)
    source_dir=output/source;source_dir.mkdir(exist_ok=True)
    inventory=export_discovery(state,source,discovery_keys,source_dir/'discovery_inventory.csv')
    summary['minimum_field_notices_saved']=len(inventory)
    summary['errors']=errors
    summary['discovery_inventory_scope']='All parsed notices with title, country and approved official record URL; includes unrelated/history records, not an Active count'
    (source_dir/'collector_run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2)+'\n')
    (source_dir/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    if own:state.close()
    if ownclient:client.close()
    return run,summary

def main():
    from collectors.opportunities import MODULES,execute as current_execute
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,choices=sorted(set(ADAPTERS)|set(MODULES)|{c+'_world_bank' for c in ['br','tr','th','mx','pe','ar']}));p.add_argument('--start');p.add_argument('--end');p.add_argument('--mode',choices=['full','incremental'],default='full');p.add_argument('--modalities',type=int,nargs='+');p.add_argument('--page-size',type=int,default=50);p.add_argument('--max-pages',type=int);p.add_argument('--resume',action='store_true');p.add_argument('--request-interval',type=float,default=1.0,help='Minimum seconds between source requests');p.add_argument('--item-limit',type=int,default=25,help='PNCP item inspection purchases: -1 all, 0 off');p.add_argument('--item-max-pages',type=int);p.add_argument('--document-list-limit',type=int,default=50,help='Attachment metadata purchases: -1 all, 0 off');p.add_argument('--document-limit',type=int,default=0);p.add_argument('--query');p.add_argument('--open-proposals',action='store_true');p.add_argument('--category');p.add_argument('--oece-pagination',choices=['page','cursor'],default='page');p.add_argument('--oece-source',choices=['seace_v3','seace_v2'],default='seace_v3');p.add_argument('--output',default='var/collectors');p.add_argument('--transport',choices=['httpx','curl'],default='httpx');p.add_argument('--retain-all',action='store_true');p.add_argument('--raw-backfill',action='store_true',help='Explicit historical/raw discovery instead of current-window verification');args=vars(p.parse_args())
    raw_backfill=args.pop('raw_backfill');active=args['source'] in MODULES and not raw_backfill
    if args['source'].endswith('_world_bank') and not active:
        # A separate historical feed, never the primary live-tender discovery channel.
        print('World Bank notices are raw/backfill candidates, not proof of current procurement status.',file=__import__('sys').stderr)
    args['start']=args['start'] or (date.today()-timedelta(days=365 if active and args['mode']=='full' else 7 if active else 6)).isoformat()
    args['end']=args['end'] or (date.today()+timedelta(days=365) if active and args['source']=='br_pncp' and args['mode']=='full' else date.today()).isoformat()
    if args['modalities'] is None:args['modalities']=[4,6] if active and args['source']=='br_pncp' else [3,5] if args['source']=='br_compras' else [6,8]
    if args['max_pages'] is not None and args['max_pages']<1:p.error('max-pages must be positive')
    if not 0.1<=args['request_interval']<=60:p.error('request-interval must be 0.1..60 seconds')
    if args['item_limit'] < -1 or args['document_list_limit'] < -1:p.error('enrichment limits must be -1, 0 or positive')
    if args['item_max_pages'] is not None and args['item_max_pages']<1:p.error('item-max-pages must be positive')
    if not 1<=args['page_size']<=1000:p.error('page-size must be 1..1000')
    if args['source']=='br_pncp' and not 10<=args['page_size']<=50:p.error('PNCP page-size must be 10..50')
    date.fromisoformat(args['start']);date.fromisoformat(args['end'])
    if args['start']>args['end']:p.error('start must be <= end')
    _,report=(current_execute if active else execute)(args.pop('source'),args);print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
