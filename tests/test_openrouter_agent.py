from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agent.openrouter_client import (OpenRouterClient, FREE_MODELS, MissingKey, ModelUnavailable,
                                      verify_model, Completion)
from agent.orchestrator import BoundedOrchestrator, AgentLimits
from agent.tools_impl import TenderTools, TOOL_SPECS


def catalog(model='nvidia/nemotron-3.5-super-vl:free', tools=True, image=True, free=True):
    return [{'id':model,'name':model,'pricing':{'prompt':'0' if free else '0.0001', 'completion':'0'},
             'supported_parameters':['tools','tool_choice'] if tools else ['max_tokens'],
             'architecture':{'input_modalities':['text','image'] if image else ['text']}}]


def tender():
    return {'record_id':'tender_valid_id',
            'source':{'tender_url':'https://procurement.mea.or.th/Procurement/Detail/example'},
            'project':{'country_code':'TH','title':'Original title','description':'Line reconstruction'},
            'procurement':{'status':'unknown','deadline_date':None,'deadline_time':None,'deadline_timezone':None},
            'assessment':{'decision':'pending_verification','missing_fields':['contacts.email']},
            'documents':[{'url':'https://procurement.mea.or.th/files_procurement/test.pdf',
                          'title':'Official tender','kind':'tender_notice'}],
            'contacts':[], 'evidence':[]}


def test_free_catalog_requires_exact_model_id_and_zero_prices():
    assert verify_model(FREE_MODELS[0], catalog(), tools=True, image=True)['zero_token_price']
    with pytest.raises(ModelUnavailable):verify_model('openai/gpt-6',catalog())
    with pytest.raises(ModelUnavailable):verify_model(FREE_MODELS[0],catalog(free=False))
    with pytest.raises(ModelUnavailable):verify_model(FREE_MODELS[0],catalog(image=False),image=True)
    with pytest.raises(ModelUnavailable):verify_model(FREE_MODELS[0],catalog(tools=False),tools=True)


def test_key_is_not_required_for_catalog_but_needed_for_inference():
    c=OpenRouterClient(api_key='')
    with pytest.raises(MissingKey):c._request('https://openrouter.ai/api/v1/chat/completions',auth=True,payload={})


def test_declared_tools_include_six_and_existing_inspect_tender():
    assert {x['function']['name'] for x in TOOL_SPECS} == {
        'inspect_tender','inspect_document','extract_image_text','translate_and_extract',
        'verify_status_and_deadline','search_official_contact','assess_commercial_relevance'}


def test_contacts_missing_is_not_deletion():
    tr=TenderTools(tender());result=tr.search_official_contact()
    assert result.status=='partial'
    assert tr.verify_status_and_deadline().facts['central_routing']=='pending_verification'


def test_document_url_not_allowlisted():
    tr=TenderTools(tender())
    res=tr.inspect_document('https://malicious.example/secret.pdf')
    assert res.status=='blocked'
    assert res.error_code=='document_url_not_in_tender'


def test_document_local_path_traversal_blocked(tmp_path):
    doc=tmp_path/'outside.pdf';doc.write_bytes(b'%PDF-1.4\n')
    root=tmp_path/'approved';root.mkdir()
    url=tender()['documents'][0]['url']
    tool=TenderTools(tender(),document_files={url:doc},document_root=root)
    res=tool.inspect_document(url)
    assert res.status=='blocked'


def test_image_only_from_approved_mapping_and_within_directory(tmp_path):
    d=tmp_path/'files';d.mkdir()
    img=d/'real.png';img.write_bytes(b'\x89PNG\r\n\x1a\nFAKE')
    tool=TenderTools(tender(),image_files={'mea_official':img},document_root=d)
    res,pixel=tool.extract_image_text('mea_official')
    assert res.status=='success'
    assert pixel['type']=='image_url'
    assert pixel['image_url']['url'].startswith('data:image/png;base64,')
    other,_=tool.extract_image_text('../passwd')
    assert other.status=='blocked'


class FakeModel:
    def __init__(self,answers):self.answers=iter(answers)
    def complete(self,model,messages,**kwargs):
        msg=next(self.answers)
        return Completion(model,msg,'stop',{'prompt_tokens':60,'completion_tokens':30,'total_tokens':90},
                          0.0,0.1,'test-response-id')


def answer_tool(name,args=None):
    return {'content':'', 'tool_calls':[{'id':'call_1','type':'function',
          'function':{'name':name,'arguments':json.dumps(args or {})}}]}


def test_real_tool_call_roundtrip_and_checkpoint(tmp_path):
    orig={'role':'assistant','content':json.dumps({'title':'Original title','evidence':[]})}
    model=FakeModel([answer_tool('inspect_tender'),answer_tool('search_official_contact'),orig])
    ckp=tmp_path/'checkpoint.json'
    report=BoundedOrchestrator(model,limits=AgentLimits(max_actions=3,max_completions=3)).run(
        tender(),FREE_MODELS[0],checkpoint=ckp)
    assert report['status']=='completed'
    assert [x['name'] for x in report['tools']]==['inspect_tender','search_official_contact']
    assert report['usage']['total_tokens']==270
    assert report['central_decision']=='pending_verification'
    assert json.loads(ckp.read_text())['status']=='completed'


def test_text_saying_tool_name_is_not_native_tool_call(tmp_path):
    model=FakeModel([{'content':'I will call inspect_tender now.'}])
    report=BoundedOrchestrator(model).run(tender(),FREE_MODELS[0])
    assert report['status']=='completed' and not report['tools']


def test_action_limit_fails_closed():
    model=FakeModel([answer_tool('inspect_tender'),answer_tool('inspect_tender')])
    report=BoundedOrchestrator(model,limits=AgentLimits(max_actions=1,max_completions=2)).run(tender(),FREE_MODELS[0])
    assert report['status']=='action_limit'
    assert len(report['tools'])==1


def test_usage_missing_fails_closed():
    class MissingUsage(FakeModel):
        def complete(self,model,messages,**kwargs):
            return Completion(model,{'content':'{}'},'stop',{},0.0,0.1,'none')
    report=BoundedOrchestrator(MissingUsage([])).run(tender(),FREE_MODELS[0])
    assert report['status']=='usage_missing'


def test_no_auto_active_when_model_proposes_it():
    fake=FakeModel([{'content':json.dumps({'status':'active','title':'fake'})}])
    res=BoundedOrchestrator(fake).run(tender(),FREE_MODELS[0])
    assert res['facts_model_proposed']['status']=='active'
    assert res['validated_central_decision']=='pending_verification'


def test_unknown_tool_is_not_executed():
    model=FakeModel([answer_tool('run_shell',{'command':'rm -rf /'}),{'content':'{}'}])
    res=BoundedOrchestrator(model).run(tender(),FREE_MODELS[0])
    assert res['status']=='completed' and len(res['tools'])==0


def test_no_cost_data_is_reported_as_unknown():
    class NoCost(FakeModel):
        def complete(self,model,messages,**kwargs):
            return Completion(model,{'content':'{}'},'stop',{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2},None,.1,'id')
    x=BoundedOrchestrator(NoCost([])).run(tender(),FREE_MODELS[0])
    assert x['status']=='completed' and not x['cost_data_complete']


def test_benchmark_synthetic_multilingual_cases_are_labeled():
    from scripts.openrouter_benchmark import MULTILINGUAL
    assert len(MULTILINGUAL)==6
    assert {x['language'] for x in MULTILINGUAL}=={'English','Portuguese','Spanish','Turkish','Thai','Indonesian'}
    assert all('SYNTHETIC' in x['text'].upper() or 'CONTROL EXAMPLE' in x['text'].upper() or 'EXEMPLO' in x['text'] or 'EJEMPLO' in x['text'] or 'ÖRNEK' in x['text'] or 'ตัวอย่าง' in x['text'] or 'CONTOH' in x['text'] for x in MULTILINGUAL)


def test_model_claim_not_promoted_to_verified_without_original_evidence():
    fake=FakeModel([{'content':json.dumps({'title':'Forged', 'evidence':[
      {'source_url':'https://fake.invalid', 'excerpt':'invented procurement detail'}]})}])
    res=BoundedOrchestrator(fake).run(tender(),FREE_MODELS[0])
    assert res['proposed_evidence_review']=={
      'submitted':1,'matched_existing_evidence':0,'accepted_as_new_facts':0}
    assert res['validated_central_decision']=='pending_verification'
