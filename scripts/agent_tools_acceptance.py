#!/usr/bin/env python3
"""Auditable all-country replay into central validator and seven safe AI tools.

Defaults to saved fixtures, not live capture. Use --run paths from real collector
outputs for network integration; keep actual collector collection separate.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.portfolio import report_row,summarize
from agent.tools_impl import TenderTools


def acceptance(paths,*,output,plans=()):
    from integration.collector_validation import validate_run
    from integration.collector_to_tender import to_tender,require_schema
    from contracts.validation.validate_tender import semantic_errors
    from integration.candidate_store import CandidateInbox
    entries=[];runs=[];failures=[]
    with TemporaryDirectory(prefix='public_research_agent_acceptance_') as folder:
        inbox=CandidateInbox(Path(folder)/'candidates.sqlite')
        for path in paths:
            path=Path(path)
            run=json.loads(path.read_text(encoding='utf-8'))
            try:validate_run(run)
            except (ValueError,KeyError) as exc:
                failures.append({'input':str(path),'error_type':type(exc).__name__});continue
            synthetic=any((t.get('raw_payload') or {}).get('synthetic_fixture') for t in run.get('tenders',[]))
            n=0
            for notice in run['tenders']:
                try:
                    tender=to_tender(run,notice)
                    require_schema(tender,'tender.v1_1.schema.json')
                    errors=semantic_errors(tender,None)
                    if errors:raise ValueError('central_business_validation')
                    inbox.upsert(notice,tender)
                    tool=TenderTools(tender)
                    try:
                        baseline=tool.inspect_tender()
                        status=tool.verify_status_and_deadline()
                        contact=tool.search_official_contact()
                        relevance=tool.assess_commercial_relevance()
                        if baseline.facts['title_original'] != tender['project']['title']:
                            raise ValueError('original_title_mismatch')
                        if status.facts['central_routing'] != tender['assessment']['decision']:
                            raise ValueError('routing_changed_by_tool')
                        row=report_row(tender,notice)
                        row['synthetic_fixture']=synthetic
                        row['tools_checked']=['inspect_tender','verify_status_and_deadline',
                                              'search_official_contact','assess_commercial_relevance']
                        row['raw_country_preserved']=(notice['country_code']==row['country'])
                        row['raw_title_preserved']=(notice['title_original']==row['title_original'])
                        entries.append(row);n+=1
                    finally:tool.close()
                except (ValueError,KeyError,TypeError) as exc:
                    failures.append({'source':run.get('source_id'),'notice_id':notice.get('source_tender_id'),
                                     'error_type':type(exc).__name__})
            runs.append({'file':str(path),'source_id':run['source_id'],'country':run['country_code'],
                         'count':n,'status':run['run_status'],'synthetic_fixture':synthetic})
        saved_count=inbox.count()
        inbox.close()
    for path in plans:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        proposals=value if isinstance(value,list) else [value]
        for proposal in proposals:
            if proposal.get('record_type')!='planned_project_proposal':
                failures.append({'file':str(path),'error_type':'not_planned_project'});continue
            tool=TenderTools(proposal)
            row=report_row(proposal);tool.close()
            if row['route']!='planned_projects':raise AssertionError('Plan leaked into tender routing')
            entries.append(row)
    result={'mode':'offline_replay_not_live','collector_runs':runs,'metrics':summarize(entries),
            'sqlite_inbox_count':saved_count,'failures':failures,'records':entries}
    if output:
        output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,action='append',default=[])
    parser.add_argument('--plan',type=Path,action='append',default=[])
    parser.add_argument('--all-indonesia-fixtures',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    runs=list(args.run)
    if args.all_indonesia_fixtures:
        runs+=sorted((ROOT/'collectors'/'indonesia'/'fixtures'/'collector_runs').glob('*.json'))
    if not runs:parser.error('Supply at least one --run or --all-indonesia-fixtures')
    report=acceptance(runs,output=args.output,plans=args.plan)
    print(json.dumps({'mode':report['mode'],'runs':report['collector_runs'],
                      'metrics':report['metrics'],'sqlite_inbox_count':report['sqlite_inbox_count'],
                      'failures_count':len(report['failures'])},ensure_ascii=False,indent=2))
    if report['failures']:raise SystemExit(1)
