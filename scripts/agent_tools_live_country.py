#!/usr/bin/env python3
"""Bounded true-network country collectors -> central validation -> seven AI tools.

Runs only pre-approved, source-specific collector commands. Keeps every validated
candidate; never elevates a model's proposal or new unverified email to Active.
Exit 0 for a blocked/empty official feed with an explicit diagnostic report.
"""
from __future__ import annotations
import argparse
import hashlib
import re
import json
import os
import subprocess
import sys
import traceback
from datetime import date,timedelta,datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.portfolio import report_row,summarize
from agent.tools_impl import TenderTools
from agent.official_research import SourcePolicy

SOURCES={
    'ID':('indonesia','pln_batam'),
    'TH':('current','th_mea'),
    'TR':('current','tr_tredas'),
    'US':('current','us_okanogan'),
    'MX':('current','mx_sidof'),
    'BR':('current','br_pncp'),
    'AR':('current','ar_energia_tenders'),
    'PE':('raw','pe_oece'),
}


def saved_document_mapping(tender:dict, raw:dict, folder:Path):
    """Reuse existing source-downloaded content-addressed documents, no re-fetch.

    Canonical CollectorRun intentionally omits local paths; for PLN Batam the
    content SHA lets us locate the bytes from this *same* new live collection.
    Trust neither arbitrary source filesystem paths nor hash declarations:
    the resolved path must remain under the invocation's output directory, and
    actual bytes must independently match SHA-256.
    """
    root=folder.resolve()
    allow_urls={x.get('url') for x in tender.get('documents') or []}
    mapped={}
    for doc in (raw.get('raw_payload') or {}).get('documents') or []:
        url=doc.get('source_url') or doc.get('url')
        sha=doc.get('sha256')
        if url not in allow_urls or not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{64}',sha):continue
        files=[folder/'capture'/'documents'/(sha+'.pdf'),folder/'capture'/'documents'/sha]
        if doc.get('local_path'):files.append(Path(doc['local_path']))
        for f in files:
            f=f.resolve()
            if not f.is_relative_to(root) or not f.is_file() or f.stat().st_size>20_000_000:continue
            if hashlib.sha256(f.read_bytes()).hexdigest()!=sha:continue
            mapped[url]=str(f)
            break
    return mapped


def run(country:str, folder:Path, *, max_runtime=420, enrich_limit=2):
    if country not in SOURCES:raise ValueError('Unsupported country')
    mode,source=SOURCES[country]
    target=folder/country;target.mkdir(parents=True,exist_ok=True)
    today=date.today();start=(today-timedelta(days=14)).isoformat();end=today.isoformat()
    if mode=='indonesia':
        command=[sys.executable,'collectors/indonesia/indonesia.py','--output',str(target/'capture'),
                 '--state-dir',str(target/'state'),'--bank-pages','1']
        runpath=target/'capture'/'collector_runs'/'pln_batam.json'
    elif mode=='raw':
        command=[sys.executable,'-m','collectors.run','--source',source,'--start',start,'--end',end,
                 '--max-pages','1','--page-size','20','--document-limit','0','--request-interval','1',
                 '--oece-pagination','cursor','--output',str(target/'capture')]
        runpath=target/'capture'/source/'collector_run.json'
    else:
        command=[sys.executable,'-m','collectors.run','--source',source,'--start',start,'--end',end,
                 '--max-pages','1','--item-limit','0','--document-limit','0','--document-list-limit','0',
                 '--request-interval','1','--output',str(target/'capture')]
        if country=='BR':command+=['--modalities','4','--page-size','20']
        runpath=target/'capture'/source/'collector_run.json'
    report={'kind':'online_collector_to_agent_tool_sample','timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'country':country,'source':source,'collector_mode':mode,'date_from':start,'date_to':end,
            'scope':'one capped source; not representative of country-wide coverage',
            'collector_exit':None,'collector_run_status':'not_run','source_candidates':0,
            'central_candidates':0,'central_validation_failures':[],
            'enriched_count':0,'enrichment':[],'all_records_retained':False,
            'online':True,'experimental':True}
    try:
        proc=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,timeout=max_runtime)
        report['collector_exit']=proc.returncode
        # Public source output may include contact data and URLs. Retain bounded
        # diagnostic tail in the artifact, not standard console output.
        report['collector_stdout_tail']=proc.stdout[-1800:]
        report['collector_stderr_tail']=proc.stderr[-1000:]
    except subprocess.TimeoutExpired:
        report['collector_exit']='timed_out'
        report['collector_run_status']='timeout'
    if runpath.is_file():
        from integration.collector_validation import validate_run
        from integration.collector_to_tender import to_tender,require_schema
        from contracts.validation.validate_tender import semantic_errors
        from integration.candidate_store import CandidateInbox
        runobj=json.loads(runpath.read_text(encoding='utf-8'))
        report['collector_run_status']=runobj.get('run_status')
        report['source_candidates']=len(runobj.get('tenders') or [])
        report['source_errors']=[e.get('error_code') for e in runobj.get('errors') or []]
        try:validate_run(runobj)
        except (ValueError,KeyError,TypeError) as exc:
            report['central_validation_failures'].append({'stage':'collector','error':str(exc)[:200]})
        inbox=CandidateInbox(target/'central_inbox.sqlite')
        entries=[];tenders=[];raw_for_id={}
        for raw in runobj.get('tenders') or []:
            try:
                tender=to_tender(runobj,raw)
                require_schema(tender,'tender.v1_1.schema.json')
                errs=semantic_errors(tender,None)
                if errs:raise ValueError('business_errors: '+','.join(errs)[:190])
                inbox.upsert(raw,tender)
                entries.append(report_row(tender,raw));tenders.append(tender)
                raw_for_id[tender["record_id"]]=raw
            except (ValueError,KeyError,TypeError) as exc:
                report['central_validation_failures'].append({'notice_id':raw.get('source_tender_id'),
                            'error_type':type(exc).__name__,'reason':str(exc)[:180]})
        report['central_candidates']=len(tenders)
        report['sqlite_inbox_count']=inbox.count()
        report['all_records_retained']=(report['source_candidates']==report['central_candidates']==inbox.count())
        report['metrics']=summarize(entries)
        # Exact candidate identity inventory is essential for a procurement
        # analyst. Published URLs may include query tokens, so redact only
        # known session/key parameters in exported public-source links. Raw
        # originals remain unmodified in source collector SQLite.
        from collectors.common.runtime import safe_url
        def redact(value):
            return safe_url(value) if isinstance(value,str) and value.startswith('https://') else value
        report['candidate_inventory']=[{
            'record_id':row['record_id'],'country':row['country'],
            'title_original':row['title_original'],
            'direct_tender_url':redact(row['direct_tender_url']),
            'official_url':redact(row['official_url']),
            'route':row['route'],'deadline_date':row['bid_deadline'],
            'deadline_timezone':row['deadline_timezone'],
            'contacts':[{**c,'evidence_url':redact(c.get('evidence_url'))}
                        for c in row['contacts']],
            'source_id':row['source_id'],
            'contact_missing':row['contact_needed'],
            'contact_verified':row['contact_verified'],
            'canonical_identity_group':row['opportunity_group_id']
        } for row in entries]
        assert len(report['candidate_inventory']) == report['central_candidates']
        inbox.close()
        policy=SourcePolicy.load(source)
        report['source_permission']={'approved_hosts':sorted(policy.approved_hosts),
            'enabled':policy.enabled,'automation_permission':policy.automation_permission}
        # Prioritize contactless / missing deadline and retain their fields regardless
        # of external availability. Only 1-2 bounded notice fetches per source.
        selected=sorted(tenders,key=lambda t:(bool(any(c.get('email') for c in t.get('contacts') or [])),
                     bool((t.get('procurement') or {}).get('deadline_date'))))[:enrich_limit]
        for tender in selected:
            document_files=saved_document_mapping(tender,raw_for_id.get(tender["record_id"],{}),target)
            tools=TenderTools(tender,enable_network=True,document_files=document_files,document_root=target)
            try:
                metadata=tools.inspect_tender()
                contacts=tools.search_official_contact()
                date_result=tools.verify_status_and_deadline()
                relevance=tools.assess_commercial_relevance()
                report['enrichment'].append({'record_id':tender['record_id'],
                    'official_url':metadata.facts.get('official_url'),
                    'original_title':metadata.facts.get('title_original'),
                    'original_country':metadata.facts.get('country'),
                    'contacts_preexisting':len(tender.get('contacts') or []),
                    'contact_candidates_found':len(contacts.facts.get('new_contact_candidates') or []),
                    'contact_evidence':contacts.facts.get('new_contact_candidates') or [],
                    'linked_vision_images':contacts.facts.get('image_ids_for_vision') or [],
                    'date_proposals':date_result.facts.get('deadline_proposals') or [],
                    'permission_issues':contacts.facts.get('network_issues') or [],
                    'source_saved_documents_reused':len(document_files),
                    'commercial_category_proposal':relevance.facts.get('categories'),
                    'central_route_unchanged':tender['assessment']['decision'],
                    'data_status':'proposed_evidence_not_verified_for_Active'})
            except (ValueError,TypeError,OSError) as exc:
                report['enrichment'].append({'record_id':tender['record_id'],'error':type(exc).__name__})
            finally:tools.close()
        report['enriched_count']=len(report['enrichment'])
    path=target/'agent_online_report.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--country',required=True,choices=sorted(SOURCES))
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--max-runtime',type=int,default=420)
    ap.add_argument('--enrich-limit',type=int,default=2)
    args=ap.parse_args()
    report=run(args.country,args.output,max_runtime=args.max_runtime,enrich_limit=args.enrich_limit)
    print(json.dumps({k:report.get(k) for k in ('country','source','collector_exit','collector_run_status','source_candidates','central_candidates','all_records_retained','enriched_count','central_validation_failures')},ensure_ascii=False))
    # Preserve diagnostics and exit cleanly when source unavailable; schema/data
    # loss is the real CI failure, not zero published notices in an official feed.
    if report['source_candidates']!=report['central_candidates']:raise SystemExit(2)
