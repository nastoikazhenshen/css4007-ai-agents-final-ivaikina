"""Compare screening on the SAME saved source rows. Never claims new network collection."""
import argparse,collections,hashlib,importlib,json,sqlite3
from pathlib import Path
from collectors.common.config import load
from collectors.common.discovery import remember,export
from collectors.common.relevance import assess
from collectors.common.runtime import CollectorState,SourceError
from collectors.opportunities import MODULES

def saved_notices(database,source):
    cfg=load(source);adapter=importlib.import_module(MODULES[source]);unique={};errors=[]
    with sqlite3.connect(database) as db:
        rows=db.execute('SELECT scope,payload,observed FROM page_rows ORDER BY observed,rowid').fetchall()
    for scope,payload,observed in rows:
        if not scope.startswith(source+':'):continue
        raw=json.loads(payload)
        try:
            parsed=adapter.parse(cfg,adapter.normalize(raw) if source=='br_compras' else raw,observed)
            key=str(parsed.get('source_tender_id') or parsed.get('tender_url'))
            unique[key]=dict(record=parsed,raw_hash=hashlib.sha256(payload.encode()).hexdigest())
        except (SourceError,KeyError,ValueError,TypeError) as e:errors.append(type(e).__name__)
    return unique,errors

def snapshot(notices,assessor=assess):
    return {key:dict(raw_hash=value['raw_hash'],verdict=assessor(value['record'].get('title_original'),value['record'].get('description_original'))['verdict']) for key,value in notices.items()}

def audit(database,source,*,baseline=None,inventory_directory=None):
    notices,errors=saved_notices(database,source);current=snapshot(notices);compared=[];different=[];gained=[];dropped=[]
    for key,value in current.items():
        prior=(baseline or {}).get(key)
        if not prior:continue
        if prior['raw_hash']!=value['raw_hash']:different.append(key);continue
        compared.append(key)
        if prior['verdict']=='excluded' and value['verdict']!='excluded':gained.append(key)
        if prior['verdict']!='excluded' and value['verdict']=='excluded':dropped.append(key)
    inventory_count=None
    if inventory_directory:
        state=CollectorState(Path(inventory_directory)/'state');keys=set();cfg=load(source)
        try:
            for item in notices.values():
                key=remember(state,cfg,item['record'])
                if key:keys.add(key)
            inventory_count=len(export(state,source,keys,Path(inventory_directory)/(source+'_inventory.csv')))
        finally:state.close()
    report=dict(source=source,execution_kind='offline_replay_of_saved_source_rows',new_network_requests=0,unique_saved_rows=len(notices),parse_error_count=len(errors),minimum_field_notices_saved=inventory_count,comparable_rows=len(compared),changed_input_rows=len(different),before_kept=sum(baseline[k]['verdict']!='excluded' for k in compared) if baseline is not None else None,after_kept=sum(current[k]['verdict']!='excluded' for k in compared) if baseline is not None else None,newly_flagged_candidates=len(gained),previously_kept_now_excluded=len(dropped),scope_verdicts=dict(collections.Counter(v['verdict'] for v in current.values())),not_a_recall_accuracy_metric='No independently labelled gold set; these are rule-difference counts, not measured recall/precision.',not_new_active_tenders=True)
    return report,current,[notices[k]['record'] for k in gained]

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',required=True);p.add_argument('--source',required=True,choices=list(MODULES));p.add_argument('--compare-to');p.add_argument('--output',default='var/recall');a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    baseline=json.loads(Path(a.compare_to).read_text()) if a.compare_to else None
    report,labels,recovered=audit(a.database,a.source,baseline=baseline,inventory_directory=out)
    for name,obj in [('report',report),('labels',labels),('recovered_candidates',recovered)]:
        (out/(a.source+'_'+name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
