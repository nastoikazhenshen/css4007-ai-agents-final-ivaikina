#!/usr/bin/env python3
"""Read-only genuine API trial of a free OpenRouter model on existing archived PDF.

Do not confuse offline execution of seven Python tools with model-selected calls.
Reports missing key, 429, errors, and truncated JSON as such, never PASS.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.openrouter_client import OpenRouterClient,FREE_MODELS
from agent.orchestrator import BoundedOrchestrator,AgentLimits
from integration.collector_to_tender import to_tender


def run(out:Path,model:str):
    report={'benchmark_type':'real_openrouter_free_model_tool_smoke',
            'selected_model':model,'key_present':bool(os.environ.get('OPENROUTER_API_KEY'))}
    if not report['key_present']:
        report['status']='not_run_missing_key'
    else:
        source=json.loads((ROOT/'tests'/'fixtures'/'collector_run_dayone_handoff.json').read_text())
        tender=to_tender(source,source['tenders'][0]); docs={}
        pdf=ROOT/'collectors'/'indonesia'/'fixtures'/'documents'/'f08e3e8baec80457a8028b042bd7301b013e77d0303417d95697a9a75550ff12.pdf'
        if pdf.is_file():
            for d in tender.get('documents') or []:
                if d.get('url'):docs[d['url']]=str(pdf)
        bounded=BoundedOrchestrator(OpenRouterClient(timeout=25),
                 limits=AgentLimits(max_actions=6,max_tokens=6500,max_seconds=125,
                                    max_documents=2,max_cost_usd=0,max_completions=6))
        result=bounded.run(tender,model,documents=docs,root=pdf.parent,
                  checkpoint=out.parent/'agent_tool_checkpoint.json')
        # The model's textual claim is NEVER a verified source fact.
        report.update(status=result['status'],actual_model_tool_calls=len(result.get('tools') or []),
            tools=result.get('tools') or [],central_decision=result.get('validated_central_decision'),
            central_validation=result.get('central_validation'),
            token_usage=result.get('usage'),cost_reported_usd=result.get('cost_reported_usd'),
            cost_data_complete=result.get('cost_data_complete'),
            evidence_review=result.get('proposed_evidence_review'),
            factual_output_valid=isinstance(result.get('facts_model_proposed'),dict),
            error=result.get('error'),elapsed_seconds=result.get('elapsed_seconds'))
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--model',choices=FREE_MODELS,default=FREE_MODELS[0])
    a=p.parse_args();r=run(a.output,a.model)
    print(json.dumps({k:r.get(k) for k in ('status','selected_model','key_present','actual_model_tool_calls','central_decision','token_usage','cost_reported_usd','factual_output_valid')},indent=2))
