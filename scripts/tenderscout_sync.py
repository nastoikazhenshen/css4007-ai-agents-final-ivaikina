#!/usr/bin/env python3
"""Lossless CollectorRun + planned-project + AI Tools integration.

Examples from repo root:
  python scripts/tenderscout_sync.py ingest --db var/tenderscout/registry.sqlite \
    --run-dir var/collectors --run-dir collectors/indonesia/fixtures/collector_runs \
    --plan-dir var/plans --output var/tenderscout/reports
  python scripts/tenderscout_sync.py collect --countries BR PE TR ID \
    --db var/tenderscout/registry.sqlite --scope bounded --max-pages 2

A successful *bounded* scan never means national coverage. No source access bypass.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from integration.unified_registry import UnifiedRegistry

# Independent country connectors; do not replace their pagination, access or
# source-specific extraction semantics. BR and US include multiple sources.
COUNTRY_SOURCES={
    'ID':['indonesia'],
    'BR':['br_pncp','br_compras'],
    'PE':['pe_oece'],
    'TR':['tr_tredas'],
    'TH':['th_mea'],
    'MX':['mx_sidof'],
    'AR':['ar_contratar','ar_energia_tenders'],
    'US':['us_nypa','us_okanogan'],
}
PLANNING={'US':'us_caiso_plan','TR':'tr_sbb_plan','AR':'ar_energia_plan'}


def find_runs(paths: Iterable[str|Path]):
    found={}
    for p in map(Path, paths):
        if p.is_file() and p.suffix=='.json':
            found[str(p.resolve())]=p
        elif p.is_dir():
            for f in p.rglob('*.json'):
                if (f.name=='collector_run.json' or (f.parent.name=='collector_runs'
                                                       and f.name!='collector_run.schema.json')):
                    found[str(f.resolve())]=f
    return list(found.values())


def find_plans(paths):
    found={}
    for p in map(Path,paths):
        if p.is_file() and p.suffix=='.json':found[str(p.resolve())]=p
        elif p.is_dir():
            for f in p.rglob('planned_projects.json'):found[str(f.resolve())]=f
    return list(found.values())


def ingest(args, *, existing=None):
    result={'collector_runs':[],'plans':[],'imported_reports':[],'errors':[]}
    owned=existing is None
    db=existing or UnifiedRegistry(args.db)
    try:
        # Previous audit CSVs are *source reports*, not current tender evidence.
        for file in args.prior_csv or []:
            try:result['imported_reports'].append({'path':str(file),
                'counts':db.import_summary_csv(file,kind='tender')})
            except Exception as exc:result['errors'].append(f'prior:{file}: {type(exc).__name__}: {str(exc)[:200]}')
        for file in args.prior_plan_csv or []:
            try:result['imported_reports'].append({'path':str(file),
                'counts':db.import_summary_csv(file,kind='planned_project')})
            except Exception as exc:result['errors'].append(f'prior_plan:{file}: {type(exc).__name__}: {str(exc)[:200]}')
        for file in find_runs((args.run or [])+(args.run_dir or [])):
            try:
                data=json.loads(file.read_text(encoding='utf-8'))
                if not isinstance(data,dict):raise ValueError('CollectorRun must be object')
                row=db.ingest_run(data)
                result['collector_runs'].append({'file':str(file),'result':row})
            except Exception as exc:
                result['errors'].append(f'collector:{file}: {type(exc).__name__}: {str(exc)[:250]}')
        for file in find_plans((args.plan_file or [])+(args.plan_dir or [])):
            try:result['plans'].append({'file':str(file),'result':db.ingest_plan_file(file)})
            except Exception as exc:result['errors'].append(f'plan:{file}: {type(exc).__name__}: {str(exc)[:250]}')
        result['enrichment']=enrich(db, limit=args.enrich_limit, network=args.allow_network)
        result['llm']=run_free_llm(db, model=args.free_model, limit=args.llm_limit)
        result['snapshot']=db.export(args.output)
        result['sqlite_integrity']=db.db.execute('PRAGMA integrity_check').fetchone()[0]
        result['candidate_inbox_count']=db.db.execute('SELECT COUNT(*) FROM candidate_inbox').fetchone()[0]
        result['source_run_count']=db.db.execute('SELECT COUNT(*) FROM source_runs').fetchone()[0]
        p=Path(args.output)/'ingestion.json'
        p.write_text(json.dumps(result,indent=2,ensure_ascii=False,default=str)+'\n',encoding='utf-8')
        return result
    finally:
        if owned:db.close()


def enrich(db: UnifiedRegistry, *, limit=0, network=False):
    """Use the *real* 7 tools, but store proposals separately from source truth.

    With network disabled tools use downloaded originals/evidence only. Model
    inference is an optional separate explicit step with a :free model.
    """
    results=[]
    if limit<=0:return results
    from agent.tools_impl import TenderTools
    desired=[r for r in db.inventory() if r['record_type']=='tender'
             and r['pipeline_bucket'] in {'future_bid_evidenced','future_bid_unverified',
                                'today_time_unverified','deadline_unknown'}]
    desired.sort(key=lambda x:(x.get('contact_needed',True) is False,
                               x['pipeline_bucket']!='future_bid_evidenced', x['record_id']))
    for row in desired[:limit]:
        record=db.get(row['record_id'])
        if not record:continue
        tools=TenderTools(record,enable_network=network)
        try:
            for name in ('inspect_tender','search_official_contact',
                         'verify_status_and_deadline','assess_commercial_relevance'):
                try:
                    resp=getattr(tools,name)()
                    db.store_research(row['record_id'],name,resp.status,resp.facts,resp.evidence)
                    results.append({'record_id':row['record_id'],'tool':name,
                                    'status':resp.status,'evidence_count':len(resp.evidence)})
                except Exception as exc:
                    results.append({'record_id':row['record_id'],'tool':name,
                                    'status':'error','error':type(exc).__name__})
        finally:tools.close()
    return results



def run_free_llm(db: UnifiedRegistry, *, model=None, limit=0):
    # This is strictly opt-in. No paid-model or automatic fallback policy.
    if not model or limit<=0:return {'status':'not_requested','attempted':0}
    from agent.openrouter_client import FREE_MODELS, OpenRouterClient
    from agent.orchestrator import BoundedOrchestrator, AgentLimits
    if model not in FREE_MODELS:raise ValueError('Only explicitly allowed :free models')
    client=OpenRouterClient(timeout=30)
    agent=BoundedOrchestrator(client,limits=AgentLimits(
        max_actions=4,max_tokens=6000,max_seconds=100,max_documents=2,
        max_cost_usd=0.0,max_completions=5))
    wanted=[r for r in db.inventory() if r.get('record_type')=='tender'
            and r.get('pipeline_bucket') in {'future_bid_evidenced','future_bid_unverified',
                                             'today_time_unverified','deadline_unknown'}]
    wanted.sort(key=lambda r:(r['pipeline_bucket']!='future_bid_evidenced',
                               not r.get('contact_needed',True),r['record_id']))
    reports=[]
    for row in wanted[:limit]:
        tender=db.get(row['record_id'])
        if not tender:continue
        try:
            result=agent.run(tender,model,enable_network=False)
            db.store_research(row['record_id'],'llm_orchestrator',result['status'],
                   {'proposed_facts':result.get('facts_model_proposed'),
                    'central_decision_unchanged':result.get('validated_central_decision'),
                    'token_usage':result.get('usage'),
                    'cost_reported_usd':result.get('cost_reported_usd'),
                    'native_tool_call_count':sum(x.get('native_tool_call_count',0)
                                                 for x in result.get('calls',[])),
                    'model_claims_not_auto_accepted':True},
                    result.get('research_evidence') or [])
            reports.append({'record_id':row['record_id'],'status':result['status'],
                            'native_tool_calls':sum(x.get('native_tool_call_count',0)
                                                 for x in result.get('calls',[])),
                            'central_route_unchanged':result.get('canonical_record_modified') is False})
        except Exception as exc:
            reports.append({'record_id':row['record_id'],
                            'status':'error','error_type':type(exc).__name__})
    return {'status':'executed','model':model,'attempted':len(reports),'reports':reports}


def collect(args):
    """Run original country-specific collectors, then retain *all* prior runs.

    An unsuccessful or empty source does not remove older rows. Date range and
    page budgets are reported; full scope must not be inferred from one run.
    """
    output=Path(args.capture_root)
    output.mkdir(parents=True,exist_ok=True)
    today=date.today()
    start=(today-timedelta(days=args.lookback_days)).isoformat()
    end=today.isoformat()
    limits=[]
    scope_pages=(None if args.scope=='full' else args.max_pages)
    for country in args.countries:
        for source in COUNTRY_SOURCES[country]:
            if source=='indonesia':
                target=output/'ID'/'indonesia'
                target.mkdir(parents=True,exist_ok=True)
                cmd=[sys.executable,'collectors/indonesia/indonesia.py',
                     '--output',str(target),'--state-dir',str(output/'ID'/'indonesia_state'),
                     '--bank-pages',str(min(15,scope_pages or 15))]
            else:
                # PNCP open-proposal endpoint uses a FUTURE submission window.
                # Publication-window sources use the historical end=today.
                # Using end=today for PNCP silently hides tenders whose bid
                # submission deadlines are in the future.
                source_end=(today+timedelta(days=args.pncp_forward_days)).isoformat() if source=='br_pncp' else end
                cmd=[sys.executable,'-m','collectors.run','--source',source,
                     '--start',start,'--end',source_end,'--output',str(output/country),
                     '--document-limit',str(args.document_limit),
                     '--request-interval','1']
                if scope_pages:cmd+=['--max-pages',str(scope_pages)]
                if source=='br_pncp':
                    cmd+=['--modalities','4','6','--item-limit',str(args.item_limit)]
                if source=='br_compras':
                    cmd+=['--modalities','3','5']
                if source=='pe_oece':
                    cmd+=['--oece-pagination','cursor','--page-size','50']
            try:
                proc=subprocess.run(cmd,cwd=ROOT,timeout=args.timeout_seconds,
                                    capture_output=True,text=True)
                limits.append({'source_id':source,'country':country,
                               'status':'completed' if proc.returncode==0 else 'collector_error',
                               'returncode':proc.returncode,
                               'message':(proc.stderr or '')[-300:]})
            except subprocess.TimeoutExpired:
                limits.append({'source_id':source,'country':country,'status':'timeout'})
        if args.include_plans and country in PLANNING:
            plan=PLANNING[country]
            cmd=[sys.executable,'-m','collectors.planning.run','--source',plan,
                 '--output',str(output/'plans')]
            if scope_pages:cmd+=['--max-pages',str(scope_pages)]
            try:
                res=subprocess.run(cmd,cwd=ROOT,timeout=args.timeout_seconds,
                                   capture_output=True,text=True)
                limits.append({'source_id':plan,'country':country,
                               'status':'completed' if res.returncode==0 else 'planner_error'})
            except subprocess.TimeoutExpired:
                limits.append({'source_id':plan,'country':country,'status':'timeout'})
    # Use the same SQLite path every time; never reset on failed fetches.
    args.run_dir=list(args.run_dir or [])+[output]
    if args.include_plans:args.plan_dir=list(args.plan_dir or [])+[output/'plans']
    result=ingest(args)
    result['live_sources']=limits
    Path(args.output).mkdir(parents=True,exist_ok=True)
    (Path(args.output)/'live_sources.json').write_text(json.dumps(limits,indent=2)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('ingest','collect'):
        a=sub.add_parser(name)
        a.add_argument('--db',default='var/tenderscout/unified_registry.sqlite3')
        a.add_argument('--output',default='var/tenderscout/reports')
        a.add_argument('--run',action='append',default=[])
        a.add_argument('--run-dir',action='append',default=[])
        a.add_argument('--plan-file',action='append',default=[])
        a.add_argument('--plan-dir',action='append',default=[])
        a.add_argument('--prior-csv',action='append',default=[])
        a.add_argument('--prior-plan-csv',action='append',default=[])
        a.add_argument('--enrich-limit',type=int,default=0)
        a.add_argument('--allow-network',action='store_true',
                       help='Only source-approved URLs, robots respected; off by default')
        a.add_argument('--free-model',default=None,
                       help='Optional explicit :free OpenRouter model; no paid fallback')
        a.add_argument('--llm-limit',type=int,default=0,
                       help='At most N agent candidates; opt-in and free-only')
        if name=='collect':
            a.add_argument('--countries',nargs='+',choices=sorted(COUNTRY_SOURCES),required=True)
            a.add_argument('--capture-root',default='var/tenderscout/captures')
            a.add_argument('--scope',choices=['bounded','full'],default='bounded')
            a.add_argument('--max-pages',type=int,default=2)
            a.add_argument('--lookback-days',type=int,default=365)
            a.add_argument('--pncp-forward-days',type=int,default=90,
                           help='Future bid-submission search horizon for PNCP open-proposals endpoint')
            a.add_argument('--document-limit',type=int,default=2)
            a.add_argument('--item-limit',type=int,default=10)
            a.add_argument('--timeout-seconds',type=int,default=480)
            a.add_argument('--include-plans',action='store_true')
    args=p.parse_args()
    if args.enrich_limit<0 or args.llm_limit<0:p.error('enrich-limit/llm-limit cannot be negative')
    if args.free_model and not args.free_model.endswith(':free'):
        p.error('Only explicitly allowed free-model IDs')
    if args.command=='collect':
        if args.lookback_days<1 or args.max_pages<1 or args.timeout_seconds<30 or not 1<=args.pncp_forward_days<=365:
            p.error('lookback days/max-pages positive; timeout>=30')
        result=collect(args)
    else:result=ingest(args)
    print(json.dumps({'db':args.db,'summary':result['snapshot'],
                      'source_runs':result['source_run_count'],
                      'inbox_rows':result['candidate_inbox_count'],
                      'ingestion_errors':len(result['errors']),
                      'live_sources':result.get('live_sources',[])},ensure_ascii=False))
    if result['snapshot']['total'] < result['candidate_inbox_count'] or result['sqlite_integrity']!='ok':
        raise SystemExit(2)


if __name__=='__main__':main()
