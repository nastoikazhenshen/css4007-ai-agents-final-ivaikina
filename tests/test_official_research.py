"""Real tool implementation controls; no synthetic data presented as real evidence."""
import json
from pathlib import Path
import pytest
import httpx
from agent.official_research import (SourcePolicy,OfficialReader,ResearchBlocked,
                                      contact_evidence,parse_dates_with_evidence,official_anchors)
from agent.tools_impl import TenderTools,TOOL_SPECS

COUNTRIES={'id':'ID','th':'TH','tr':'TR','us':'US','mx':'MX','br':'BR','ar':'AR','pe':'PE'}

def config(tmp_path,source_id='br_pncp',country='BR',host='pncp.gov.br',enabled=True):
    d=tmp_path/'collectors'/'sources';d.mkdir(parents=True,exist_ok=True)
    obj={'source_id':source_id,'country_code':country,'enabled':enabled,
         'automation_permission':'confirmed_permitted' if enabled else 'unverified',
         'approved_hosts':[host], 'endpoints':[{'url':f'https://{host}/api/v1/contratacoes'}]}
    (d/f'{source_id}.json').write_text(json.dumps(obj))
    return SourcePolicy.load(source_id,tmp_path)

def tender(country='BR',source='br_pncp',host='pncp.gov.br',email=None):
    url=f'https://{host}/notice/ABC123'
    return {'record_id':'tender_ABC123', 'schema_version':'1.1.0',
       'source':{'source_id':source,'tender_url':url,'source_url':url},
       'project':{'title':'Construção de linha de transmissão 150 kV','country_code':country,
                  'description':'Supply of insulators','buyer_name':'Utility'},
       'procurement':{'deadline_date':'2026-12-18','deadline_time':'14:00',
                      'deadline_timezone':'America/Sao_Paulo','status':'unknown'},
       'contacts':[] if email is None else [{'email':email,'name':None,'phone':None,
           'role':'Tender department','verified':False,'evidence_url':None}],
       'documents':[{'url':f'https://{host}/documents/ABC123.pdf','title':'Official notice','kind':'tender_notice'}],
       'assessment':{'decision':'pending_verification','missing_fields':['contacts.email']},
       'evidence':[]}

def reader(tmp_path, htmlpage, *, robots='User-agent: *\nAllow: /', contact_page=None):
    pol=config(tmp_path)
    def handler(req):
        if req.url.path=='/robots.txt':return httpx.Response(200,text=robots)
        if req.url.path=='/notice/ABC123':return httpx.Response(200,text=htmlpage,headers={'content-type':'text/html'})
        if req.url.path=='/contact':return httpx.Response(200,text=contact_page or '',headers={'content-type':'text/html'})
        if req.url.path=='/documents/ABC123.pdf':return httpx.Response(403)
        return httpx.Response(404)
    return OfficialReader(pol,client=httpx.Client(transport=httpx.MockTransport(handler)),max_requests=4)

def test_all_eight_countries_registry_policy(tmp_path):
    for country,cc in COUNTRIES.items():
        pol=config(tmp_path,'s_'+country,cc,host='official-'+country+'.example')
        assert pol.country_code==cc
        assert pol.permitted('https://official-'+country+'.example/tender/100')
        assert not pol.permitted('https://evil.example/tender/100')
        assert not pol.permitted('http://official-'+country+'.example/tender/100')

def test_nonwhitelisted_source_fails_closed(tmp_path):
    pol=SourcePolicy.load('unknown_source',tmp_path)
    assert not pol.permitted('https://official.example/anything')
    assert not pol.enabled

def test_robot_disallow_blocks_without_request(tmp_path):
    page='<h1>Bid closing 18 December 2026</h1>'
    rd=reader(tmp_path,page,robots='User-agent: *\nDisallow: /')
    with pytest.raises(ResearchBlocked,match='robots'):
        rd.get('https://pncp.gov.br/notice/ABC123')
    assert rd.requests==0

def test_access_restricted_does_not_drop_card(tmp_path):
    rd=reader(tmp_path,'<h1>ABC</h1>')
    t=tender()
    tr=TenderTools(t,reader=rd,enable_network=True,registry_root=tmp_path)
    result=tr.inspect_document(t['documents'][0]['url'])
    assert result.status=='partial' and result.error_code=='document_not_downloaded'
    assert tr.inspect_tender().facts['official_url']==t['source']['tender_url']
    assert len(t['documents'])==1
    tr.close()

def test_official_contact_with_role_evidence(tmp_path):
    html='<h1>ABC123</h1><p>For tender clarifications contact: procurement@utility.example</p>'
    rd=reader(tmp_path,html)
    t=tender()
    tr=TenderTools(t,reader=rd,enable_network=True,registry_root=tmp_path)
    res=tr.search_official_contact()
    assert res.status=='success'
    assert res.facts['new_contact_candidates'][0]['email']=='procurement@utility.example'
    assert res.facts['new_contact_candidates'][0]['procurement_role_verified']
    assert len(t['contacts'])==0  # never silently mutate canonical record
    tr.close()

def test_generic_web_contact_is_not_verified(tmp_path):
    html='<h1>ABC123</h1><a href="/contact">Contact support</a>'
    page='<div>Press office: press@utility.example</div>'
    rd=reader(tmp_path,html,contact_page=page)
    tr=TenderTools(tender(),reader=rd,enable_network=True,registry_root=tmp_path)
    res=tr.search_official_contact()
    assert res.facts['new_contact_candidates'][0]['email']=='press@utility.example'
    assert res.facts['new_contact_candidates'][0]['procurement_role_verified'] is False
    tr.close()

def test_unknown_email_is_not_required_for_retention():
    t=tender(email=None)
    tools=TenderTools(t)
    assert tools.inspect_tender().facts['contact_missing_does_not_drop_candidate']
    assert tools.verify_status_and_deadline().facts['deadline_date']=='2026-12-18'
    assert tools.verify_status_and_deadline().facts['central_routing']=='pending_verification'
    tools.close()

def test_confirmed_deadline_without_email_stays_pending_by_existing_rules():
    t=tender(email=None);t['evidence']=[{'source_url':t['source']['tender_url'],
        'field_path':'procurement.deadline_date','excerpt':'Bids close 18 December 2026 at 14:00'}]
    res=TenderTools(t).verify_status_and_deadline()
    assert res.facts['deadline_date']=='2026-12-18'
    assert res.facts['central_routing']=='pending_verification'
    assert len(res.evidence)==1

def test_approved_urls_not_arbitrary_following(tmp_path):
    rd=reader(tmp_path,'<h1>ABC123</h1><a href="https://attacker.example/contact">contact</a>')
    tr=TenderTools(tender(),reader=rd,enable_network=True,registry_root=tmp_path)
    x=tr.search_official_contact()
    assert 'https://pncp.gov.br/notice/ABC123' in x.facts['official_pages_checked']
    assert not any('attacker' in url for url in x.facts['official_pages_checked'])
    tr.close()

def test_deadline_portuguese_and_indonesian():
    a=parse_dates_with_evidence('Envio de propostas até 18 de dezembro de 2026 às 14:00, horário de Brasília.','https://pncp.gov.br/a')
    b=parse_dates_with_evidence('Batas pemasukan penawaran tanggal 18 Desember 2026 pukul 14.00 WIB.','https://plnbatam.com/a')
    assert a[0]['deadline_date_proposed']=='2026-12-18'
    assert b[0]['deadline_date_proposed']=='2026-12-18'
    assert b[0]['deadline_time_proposed']=='14:00'
    assert b[0]['timezone_proposed']=='Asia/Jakarta'

def test_deadline_avoids_registration_only():
    x=parse_dates_with_evidence('Pendaftaran peserta tanggal 5 Oktober 2026 pukul 14.00 WIB.','https://plnbatam.com/a')
    assert not x

def test_thai_buddhist_date():
    x=parse_dates_with_evidence('ยื่นข้อเสนอ 18 ธันวาคม 2569 เวลา 14:00 น. เวลาประเทศไทย','https://procurement.mea.or.th/notice')
    assert x and x[0]['deadline_date_proposed']=='2026-12-18'

def test_contact_without_procurement_role_not_auto_verified():
    x=contact_evidence('Visit us at: admin@example.org','https://official.example/a',notice_specific=True)
    assert x and not x[0]['procurement_role_verified']

def test_planned_project_has_separate_stage(monkeypatch):
    import sys,types
    commercial=types.ModuleType("integration.commercial")
    commercial.initial_assessment=lambda title,desc: (["overhead_line"],"potential","unknown","yes")
    integration=types.ModuleType("integration");integration.commercial=commercial
    monkeypatch.setitem(sys.modules,"integration",integration)
    monkeypatch.setitem(sys.modules,"integration.commercial",commercial)
    project={'record_type':'planned_project_proposal','title_original':'220 kV future OHL network upgrade',
       'country_code':'TR','official_plan_url':'https://www.sbb.gov.tr/plan/2027',
       'source':{'source_id':'tr_sbb_plan'},'evidence':[],'contact_hints':[]}
    tr=TenderTools(project)
    assert tr.inspect_tender().facts['planned_project_not_active_tender']
    assert tr.inspect_tender().facts['central_route']=='planned_projects'
    assert tr.assess_commercial_relevance().facts['project_stage']=='planned_project'
    tr.close()

def test_all_seven_tools_declared():
    assert {x['function']['name'] for x in TOOL_SPECS}=={'inspect_tender','inspect_document','extract_image_text',
        'translate_and_extract','verify_status_and_deadline','search_official_contact','assess_commercial_relevance'}

def test_mea_email_image_discovery_and_vision_next_turn(tmp_path):
    import base64
    import io
    import fitz
    pol=config(tmp_path,'th_mea','TH','procurement.mea.or.th')
    url='https://procurement.mea.or.th/notice/ABC123'
    image=b'\x89PNG\r\n\x1a\n' + b'0'*20
    def handler(req):
        if req.url.path=='/robots.txt':return httpx.Response(404)
        if req.url.path=='/notice/ABC123':return httpx.Response(200,headers={'content-type':'text/html'},
             text='<h1>MEA Bid</h1><p>Email: <img alt="Email" src="/pictures/mail.png"></p>')
        if req.url.path=='/pictures/mail.png':return httpx.Response(200,content=image,headers={'content-type':'image/png'})
        return httpx.Response(404)
    rd=OfficialReader(pol,client=httpx.Client(transport=httpx.MockTransport(handler)))
    t=tender(country='TH',source='th_mea',host='procurement.mea.or.th')
    tr=TenderTools(t,reader=rd,enable_network=True,registry_root=tmp_path)
    res=tr.search_official_contact()
    ids=res.facts['image_ids_for_vision'];assert len(ids)==1
    result,img=tr.extract_image_text(ids[0]);assert result.status=='success'
    assert base64.b64decode(img['image_url']['url'].split(',')[1])==image
    assert result.facts['ocr_status']=='pending_model_response_verification'
    tr.close()


def test_saved_scanned_pdf_returns_image_ids(tmp_path,monkeypatch):
    import sys,types,hashlib
    mod=types.ModuleType("collectors.common.document_text")
    mod.extract=lambda path,**kwargs: {"parts":[{"text":"","locator":"PDF","sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"needs_ocr":True,"format":"pdf","pages":1}],"issues":[{"reason":"ocr_required"}]}
    monkeypatch.setitem(sys.modules,"collectors",types.ModuleType("collectors"))
    monkeypatch.setitem(sys.modules,"collectors.common",types.ModuleType("collectors.common"))
    monkeypatch.setitem(sys.modules,"collectors.common.document_text",mod)
    import fitz
    url='https://pncp.gov.br/documents/ABC123.pdf'
    d=fitz.open();page=d.new_page();page.draw_rect(fitz.Rect(10,10,90,90),color=(0,0,0));
    folder=tmp_path/'saved';folder.mkdir();path=folder/'scan.pdf';d.save(path);d.close()
    t=tender()
    tr=TenderTools(t,document_files={url:path},document_root=folder)
    result=tr.inspect_document(url)
    assert result.status=='partial'
    ids=result.facts['image_ids_for_vision'];assert len(ids)>0
    imgres,img=tr.extract_image_text(ids[0]);assert imgres.status=='success'
    tr.close()


def test_source_registry_disabled_blocks_model_fetch(tmp_path):
    config(tmp_path,enabled=False)
    pol=SourcePolicy.load('br_pncp',tmp_path)
    rd=OfficialReader(pol,client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200))))
    tr=TenderTools(tender(),reader=rd,enable_network=True,registry_root=tmp_path)
    res=tr.search_official_contact()
    assert res.status=='partial'
    assert res.facts['network_issues'][0]['reason']=='source_not_permitted'
    tr.close()


def test_legacy_indonesia_policies_are_specific_and_spse_blocked(tmp_path):
    pln=SourcePolicy.load('pln_batam',tmp_path)
    wb=SourcePolicy.load('world_bank',tmp_path)
    assert pln.country_code=='ID' and pln.permitted('https://www.plnbatam.com/wp-content/uploads/a.pdf')
    assert wb.country_code=='ID' and wb.permitted('https://search.worldbank.org/api/v2/procnotices')
    assert not SourcePolicy.load('spse_esdm',tmp_path).permitted('https://spse.inaproc.id/esdm/lelang')
    assert not SourcePolicy.load('pln_vms',tmp_path).permitted('https://vms.pln.co.id/')
