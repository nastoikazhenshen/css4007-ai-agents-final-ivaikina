"""Synthetic control cases for current-window proof and contextual scope, not live counts."""
import copy,json,subprocess
from datetime import datetime,timezone,timedelta
from pathlib import Path
import httpx,pytest
from collectors.common.config import load
from collectors.common.records import notice
from collectors.common.runtime import CollectorState,Fetcher,CurlTransport,SourceError,safe_url,schema
from collectors.common.relevance import assess
from collectors.common.opportunity import decide,instant_bounds
from collectors.brazil import pncp
from collectors.thailand import mea
from collectors.argentina import contratar
from collectors.turkey import tredas
from collectors.usa import okanogan
from collectors.opportunities import execute

NOW=datetime(2026,11,22,12,tzinfo=timezone.utc)
def purchase():
    d=dict(numeroControlePNCP='12345678000190-1-000001/2026',anoCompra=2026,sequencialCompra=1,orgaoEntidade={'cnpj':'12345678000190','razaoSocial':'Synthetic Grid Buyer'},objetoCompra='Reconstruction of an overhead transmission line',situacaoCompraId=1,situacaoCompraNome='Divulgada no PNCP',dataAberturaProposta='2026-11-01T08:00:00-03:00',dataEncerramentoProposta='2026-12-01T12:00:00-03:00',valorTotalHomologado=None)
    r=pncp.parse(load('br_pncp'),d,NOW.isoformat())
    r['raw_payload'].update(verified_detail=copy.deepcopy(d),detail_url=r['tender_url'],detail_checked_at=NOW.isoformat())
    return r

@pytest.mark.parametrize('title',[
 'Overhead scanner with book cradle','Door hardware and glass doors',
 'Aquisição canoa havaiana','Rede de distribuição de água potável',
 'Rede de distribuição de gás natural','Sistema de bombeamento do poço tubular com instalações elétricas e rede de distribuição',
 'Underground cable line construction','จ้างก่อสร้างท่อร้อยสายไฟฟ้าใต้ดิน สายส่ง 115 kV',
 'Building internal wiring repair','Vegetation clearance and right of way mowing'])
def test_unrelated_or_underground_not_promoted(title):assert assess(title)['verdict']=='excluded'

@pytest.mark.parametrize('title',[
 'Rebuild 11 kV overhead line','HVDC transmission line construction',
 'Reconstrução de linha de transmissão','Construcción línea aérea',
 'ENERJİ İLETİM HATTI yapımı','งานก่อสร้างสายส่งอากาศและสายป้อนอากาศ',
 'ลูกถ้วยแขวน','59,000 m Guy Wire','Parallel Groove Clamp for Aluminum Conductor',
 'SUTT reconstruction'])
def test_target_without_min_voltage_volume_or_contact(title):assert assess(title)['verdict']=='relevant'

@pytest.mark.parametrize('title',[
 'Manutenção preventiva e corretiva de rede de distribuição de energia elétrica',
 'Sistema de microgeração fotovoltaica com conexão na rede elétrica'])
def test_uncertain_scope_stays_for_documents(title):assert assess(title)['verdict']=='needs_documents'

def test_current_pncp_purchase_requires_source_detail_but_not_email():
    r=purchase();assert not r['contact_hints'];assert decide(r,'br_pncp',now=NOW)['state']=='open_verified'
    del r['raw_payload']['verified_detail'];assert decide(r,'br_pncp',now=NOW)['state']=='pending_verification'

@pytest.mark.parametrize('field,value',[
 ('situacaoCompraId',2),('situacaoCompraNome','Suspensa'),('contratacaoExcluida',True),
 ('valorTotalHomologado',1),('numeroControlePNCP','different-id'),
 ('dataAberturaProposta','2026-12-20T12:00:00Z'),
 ('dataEncerramentoProposta','2026-12-30T12:00:00Z')])
def test_incomplete_conflicting_or_closed_detail_does_not_open(field,value):
    r=purchase();r['raw_payload']['verified_detail'][field]=value
    assert decide(r,'br_pncp',now=NOW)['state']!='open_verified'

@pytest.mark.parametrize('status',['cancelled','Revogada','Suspensa','Awarded','Pending Selection','anulada'])
def test_terminal_status_never_open(status):
    r=purchase();r['raw_status']=status;assert decide(r,'br_pncp',now=NOW)['state']=='closed_or_unavailable'

@pytest.mark.parametrize('age',[timedelta(hours=25),timedelta(minutes=-6)])
def test_stale_or_future_observation_not_open(age):
    r=purchase();r['raw_payload']['detail_checked_at']=(NOW-age).isoformat()
    assert decide(r,'br_pncp',now=NOW)['state']=='pending_verification'

def test_timezone_is_not_invented():
    earliest,latest,method=instant_bounds('2026-11-22T12:00:00')
    assert latest-earliest==timedelta(hours=26) and 'unresolved' in method
    r=purchase();r['deadline_text']='2026-11-22T12:00:00';r['raw_payload']['verified_detail']['dataEncerramentoProposta']=r['deadline_text']
    assert decide(r,'br_pncp',now=NOW)['state']=='pending_verification'

def test_past_deadline_is_history_even_if_page_still_published():
    r=purchase();r['deadline_text']='2026-11-01T10:00:00Z'
    assert decide(r,'br_pncp',now=NOW)['state']=='closed'

def test_ar_opening_not_accepted_as_submission_cutoff():
    row=dict(id='SYNTHETIC-1',title='Construcción línea aérea',opening='14/12/2026 12:00',status='Publicado',buyer='Synthetic Buyer')
    r=contratar.parse(load('ar_contratar'),row,NOW.isoformat());assert r['deadline_text'] is None
    r['raw_payload'].update(detail_url='https://contratar.gob.ar/example',detail_checked_at=NOW.isoformat(),verified_detail=dict(process_number='SYNTHETIC-1'))
    r['tender_url']='https://contratar.gob.ar/example'
    assert decide(r,'ar_contratar',now=NOW)['state']=='pending_verification'
    r['deadline_text']='2026-12-14T11:00:00';r['raw_payload']['verified_detail'].update(submission_deadline=r['deadline_text'],deadline_basis='physical_documents_submission')
    assert decide(r,'ar_contratar',now=NOW)['state']=='open_verified'

def test_thai_year_and_bid_closing_time_are_explicit():
    assert mea.thai_date('๒ ธ.ค. ๖๙','12:00')=='2026-12-02T12:00:00+07:00'
    assert mea.thai_date('2 ธ.ค. 2569','12:00')=='2026-12-02T12:00:00+07:00'
    assert mea.thai_date('not a date') is None

def test_mea_detail_contact_and_schedule_without_email_is_kept(tmp_path):
    row=dict(KEY_ID='synthetic-id',SUBJECT='งานก่อสร้างสายส่งอากาศ',DETAIL_DT_TEXT='20 พ.ย. 69',BID_DT_TEXT='2 ธ.ค. 69')
    r=mea.parse(load('th_mea'),row,NOW.isoformat());assert r['deadline_text'] is None
    body='''<table class="detailTable">
      <tr><th>ชื่อโครงการที่จัดซื้อจัดจ้าง</th><td>งานก่อสร้างสายส่งอากาศ</td></tr>
      <tr><th>กำหนดเสนอราคา</th><td>2 ธ.ค. 69 เวลา 09:00 - 12:00 น.</td></tr>
      <tr><th>กำหนดระยะเวลาประกาศและซื้อแบบประกวดราคาอิเล็กทรอนิกส์</th><td>20 พ.ย. 69 - 2 ธ.ค. 69</td></tr>
      <tr><th>สอบถามทางโทรศัพท์</th><td>000000000</td></tr>
      <tr><th></th><td>ผู้ลงประกาศ : Synthetic Buyer ตำแหน่ง : Procurement Email : <img src="data:image/png;base64,AA=="/></td></tr>
      <tr><th>เอกสาร</th><td><a href="/spec.pdf" download="spec.pdf">PDF</a></td></tr></table>'''
    st=CollectorState(tmp_path);c=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,text=body)))
    f=Fetcher(load('th_mea'),st,c,sleep=lambda _:None);mea.enrich(f,r)
    assert r['deadline_text'].endswith('12:00:00+07:00') and r['contact_hints'][0]['name']=='Synthetic Buyer'
    assert r['contact_hints'][0]['email'] is None and r['documents'][0]['url'].endswith('/spec.pdf')
    r['raw_payload']['detail_checked_at']=NOW.isoformat();assert decide(r,'th_mea',now=NOW)['state']=='open_verified'
    r['raw_payload']['verified_detail']['document_sale_end']='2026-11-20T23:59:00+07:00'
    assert decide(r,'th_mea',now=NOW)['state']=='pending_verification';st.close();c.close()

@pytest.mark.parametrize('module,body',[(mea,{'data':[]}), (tredas,{'data':[]})])
def test_missing_native_total_does_not_mean_zero(module,body):
    with pytest.raises(SourceError):module.page(body,module.SEARCH+'?start=0&length=100&draw=1' if module is mea else tredas.URL+'?start=0&length=100&draw=1')

def test_public_datatables_next_page():
    p=mea.page(dict(data=[dict(KEY_ID='synthetic')],recordsFiltered=3),mea.SEARCH+'?start=0&length=1&draw=1')
    assert p.total==3 and 'start=1' in p.next_url

def test_non_public_post_refused_before_network(tmp_path):
    st=CollectorState(tmp_path);f=Fetcher(load('th_mea'),st,httpx.Client(transport=httpx.MockTransport(lambda r:pytest.fail('Network must not be called'))),sleep=lambda _:None)
    with pytest.raises(SourceError):f.get('https://procurement.mea.or.th/account/login',form={'password':'synthetic'})
    st.close()

def test_session_urls_redacted():
    u=safe_url('https://example.test/page;jsessionid=SYNTHETIC?sid=SYNTHETIC&api_key=SYNTHETIC&id=record')
    assert 'SYNTHETIC' not in u and 'id=record' in u

def test_curl_preserves_public_session_cookie_only_in_memory(monkeypatch):
    def run(cmd,**kwargs):
        Path(cmd[cmd.index('--dump-header')+1]).write_text('HTTP/1.1 200 OK\nSet-Cookie: public_session=synthetic; Path=/; Secure\nContent-Type: text/html\n\n')
        Path(cmd[cmd.index('--output')+1]).write_bytes(b'Public list')
        return subprocess.CompletedProcess(cmd,0,stdout='200',stderr='')
    monkeypatch.setattr(subprocess,'run',run)
    with httpx.Client(transport=CurlTransport(),trust_env=False) as c:
        c.get('https://example.test/');assert c.cookies.get('public_session')=='synthetic'

def test_complete_live_scope_pipeline_and_repeat_without_email(tmp_path):
    row=purchase()['raw_payload']['verified_detail'];row['dataEncerramentoProposta']='2099-12-01T12:00:00Z'
    def reply(req):
        if '/contratacoes/' in str(req.url):return httpx.Response(200,json=dict(data=[row],totalRegistros=1,totalPaginas=1,numeroPagina=1))
        return httpx.Response(200,json=row)
    c=httpx.Client(transport=httpx.MockTransport(reply));options=dict(start='2026-01-01',end='2099-12-31',modalities=[4],output=str(tmp_path),document_limit=0)
    run,report=execute('br_pncp',options,client=c);schema(run,'collector_run.schema.json')
    assert report['outcomes']['open_verified']==1 and report['central_records']==1 and not report['central_error']
    _,repeat=execute('br_pncp',options,client=c);assert repeat['changes']==dict(new=0,changed=0,unchanged=1)
    assert repeat['sqlite_integrity']=='ok';c.close()


def test_primary_cli_uses_correct_compras_modality(monkeypatch,capsys):
    import sys
    import collectors.run as cli
    import collectors.opportunities as active
    captured={}
    def execute(source,options):captured.update(source=source,options=options);return {},{'test':'synthetic'}
    monkeypatch.setattr(active,'execute',execute)
    monkeypatch.setattr(sys,'argv',['collectors.run','--source','br_compras'])
    cli.main();assert captured['options']['modalities']==[3,5]


def test_cross_feed_identity_and_probable_duplicates_are_lossless():
    from collectors.portfolio import build
    from collectors.common.runtime import utc
    def run(source,rs):
        return dict(schema_version='1.0.0',run_id='synthetic-'+source,source_id=source,country_code='BR',started_at=NOW.isoformat(),finished_at=NOW.isoformat(),run_status='success',coverage=dict(verified_complete=True,reason='Synthetic control only',date_from=None,date_to=None,next_cursor=None),metrics=dict(pages_requested=1,pages_received=1,records_seen=len(rs),records_emitted=len(rs),errors_count=0),http_observations=[],errors=[],tenders=rs)
    one=purchase();one['raw_payload']['verified_detail']['valorTotalEstimado']=100
    copy_id=copy.deepcopy(one);copy_id['source_tender_id']='12345678000190-1-000002/2026'
    copy_id['raw_payload']['canonical_identity']['value']=copy_id['source_tender_id']
    copy_id['raw_payload']['verified_detail']['numeroControlePNCP']=copy_id['source_tender_id']
    portfolio=build([run('br_pncp',[one,copy_id]),run('br_compras',[copy.deepcopy(one)])],now=NOW)
    assert portfolio['summary']['retained_identities']==2
    assert portfolio['summary']['open_official_identities']==2
    assert portfolio['summary']['possible_distinct_open_groups']==1
    assert len(portfolio['entries'][0]['source_aliases'])==2
    assert not portfolio['entries'][0]['contacts']


def test_portfolio_reevaluates_expired_proof_and_preserves_closed_status():
    from collectors.portfolio import build
    # Source proof older than 24h cannot be shown as verified-open in a later report.
    r=purchase()
    assert decide(r,'br_pncp',now=NOW+timedelta(days=2))['state']=='pending_verification'


def test_pdf_requires_explicit_clarification_instruction_before_attribution():
    from collectors.common.contact_attribution import procurement_pdf_contacts
    r=purchase();url='https://pncp.gov.br/synthetic.pdf'
    procurement_pdf_contacts(r,'Supplier: vendor@example.test. Audit firm audit@example.test.',url)
    assert not r['contact_hints']
    procurement_pdf_contacts(r,'A impugnação e o pedido de esclarecimento poderão ser realizados por forma eletrônica, pelos seguintes meios: office@example.test',url)
    assert r['contact_hints'][0]['email']=='office@example.test'
    assert r['raw_payload']['contacts'][0]['contact_type']=='tender_department'
    assert r['raw_payload']['evidence']
    procurement_pdf_contacts(r,'A impugnação e o pedido de esclarecimento poderão ser realizados por forma eletrônica, pelos seguintes meios: office@example.test',url)
    assert len(r['contact_hints'])==1


def test_incremental_rechecks_old_open_card_even_when_not_in_new_pages(tmp_path):
    old=purchase();old['deadline_text']='2099-12-01T12:00:00Z';old['raw_payload']['verified_detail']['dataEncerramentoProposta']=old['deadline_text']
    st=CollectorState(tmp_path/'state');st.merge_records('br_pncp',[old]);st.close()
    requested=[]
    def reply(req):
        requested.append(str(req.url))
        if '/contratacoes/' in str(req.url):return httpx.Response(200,json=dict(data=[],totalRegistros=0,totalPaginas=0,numeroPagina=1))
        return httpx.Response(403,text='Forbidden')
    c=httpx.Client(transport=httpx.MockTransport(reply))
    run,report=execute('br_pncp',dict(start='2026-11-21',end='2026-11-22',mode='incremental',modalities=[4],output=str(tmp_path),document_limit=0),client=c)
    assert len(run['tenders'])==1 and report['state_candidates_rechecked']==1
    assert run['tenders'][0]['raw_payload']['verification']['state']=='pending_verification'
    assert 'atualizacao?' in requested[0] and len(requested)==2
    assert run['tenders'][0]['raw_payload']['detail_error']['code']=='http_403'
    c.close()


def test_offline_reanalysis_detects_corrupt_document_and_does_not_claim_network(tmp_path):
    from collectors.recheck import recheck
    r=purchase();r['raw_payload']['detail_checked_at']=__import__('collectors.common.runtime',fromlist=['utc']).utc()
    bad=tmp_path/'bad.pdf';bad.write_bytes(b'corrupt-file')
    r['raw_payload']['documents']=[dict(local_path=str(bad),sha256='0'*64,source_url='https://pncp.gov.br/synthetic.pdf')]
    original=dict(schema_version='1.0.0',run_id='synthetic-live-capture',source_id='br_pncp',country_code='BR',started_at=NOW.isoformat(),finished_at=NOW.isoformat(),run_status='success',coverage=dict(verified_complete=True,reason='Synthetic control',date_from=None,date_to=None,next_cursor=None),metrics=dict(pages_requested=1,pages_received=1,records_seen=1,records_emitted=1,errors_count=0),http_observations=[],errors=[],tenders=[r])
    run,report=recheck(original,directory=tmp_path/'reanalysis')
    assert not run['http_observations'] and run['metrics']['pages_requested']==0
    assert run['tenders'][0]['raw_payload']['reanalysis_errors'][0]['reason']=='Document hash mismatch'
    assert report['execution_kind']=='offline_reanalysis_of_live_capture'
    assert report['central_records']==1 and report['repeat']['new']==0


@pytest.mark.parametrize('invalid',[
    {'messageCode':200,'NotasMatutinas':[]},
    {'messageCode':400},
    {'messageCode':200,'NotasMatutinas':[{'codNota':1,'fecha':'13-06-2016'}],'NotasVespertinas':[],'NotasExtraordinarias':[]}
])
def test_sidof_never_uses_documentation_example_or_missing_editions(invalid):
    from collectors.mexico import sidof
    with pytest.raises(SourceError):sidof.page(invalid,sidof.API+'/20-11-2026')


def test_sidof_all_editions_and_hidden_cfe_scope():
    from collectors.mexico import sidof
    body=dict(messageCode=200,NotasMatutinas=[dict(codNota=1,fecha='20-11-2026',titulo='Synthetic invitation',codOrgaDos='COMISION FEDERAL DE ELECTRICIDAD')],NotasVespertinas=[dict(codNota=2,fecha='20-11-2026')],NotasExtraordinarias=[])
    p=sidof.page(body,sidof.API+'/20-11-2026');assert len(p.rows)==p.total==2 and p.next_url is None
    assert sidof.must_inspect(p.rows[0]);r=sidof.parse(load('mx_sidof'),p.rows[0],NOW.isoformat())
    assert r['published_on']=='2026-11-20' and r['deadline_text'] is None


def test_sidof_publication_contact_does_not_create_current_status(tmp_path):
    from collectors.mexico import sidof
    row=dict(codNota=1,fecha='20-11-2026',titulo='Synthetic invitation',codOrgaDos='CFE')
    r=sidof.parse(load('mx_sidof'),row,NOW.isoformat())
    obj={'messageCode':200,'Nota':{'codNota':1,'cadenaContenido':'<p>Construcción línea aérea. CFE-0004-EXAMPLE-2026. Correo electrónico: office@example.test. Diferentes procesos de contratación.</p>'}}
    st=CollectorState(tmp_path);c=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(404) if str(req.url).endswith('/robots.txt') else httpx.Response(200,json=obj)))
    f=Fetcher(load('mx_sidof'),st,c,sleep=lambda _:None);sidof.enrich(f,r)
    assert r['contact_hints'][0]['email']=='office@example.test'
    assert r['raw_payload']['discovered_contest_ids'] and not r['deadline_text']
    assert decide(r,'mx_sidof',now=NOW)['state']=='pending_verification';st.close();c.close()


def test_listing_forbidden_stops_source_partitions_without_bypass(tmp_path):
    requests=[]
    def reply(req):requests.append(str(req.url));return httpx.Response(403,text='Forbidden')
    c=httpx.Client(transport=httpx.MockTransport(reply))
    run,report=execute('br_pncp',dict(start='2026-11-01',end='2026-12-31',modalities=[4,6],output=str(tmp_path),document_limit=10),client=c)
    assert len(requests)==1 and run['run_status']=='blocked'
    assert not run['coverage']['verified_complete'] and not run['tenders']
    assert report['errors'][0]['error_code']=='http_403';c.close()


def test_sidof_epc_body_title_and_offer_event_are_not_guessed():
    from collectors.mexico import sidof
    row=dict(codNota=1,fecha='20-11-2026',titulo='SYNTHETIC generic publication heading',codOrgaDos='CFE')
    r=sidof.parse(load('mx_sidof'),row,NOW.isoformat())
    body='CFE-0004-EXAMPLE-2026, para la contratación de los trabajos de la 999 SLT Suministro de energía eléctrica de ejemplo. Los interesados pueden consultar. Presentación de Ofertas y Apertura de Ofertas Técnicas: 15 de enero de 2027 10:00 horas El personal de contacto es Synthetic Procurement Group, con correo electrónico office@example.test.'
    sidof.apply_detail(r,dict(cadenaContenido=body),sidof.API+'/nota/1',NOW.isoformat())
    assert r['title_original']=='999 SLT Suministro de energía eléctrica de ejemplo'
    assert r['deadline_text']=='2027-01-15T10:00:00' and r['contact_hints'][0]['email']=='office@example.test'
    assert decide(r,'mx_sidof',now=NOW)['state']=='pending_verification'


def test_sidof_explicit_award_notice_goes_to_history():
    from collectors.mexico import sidof
    r=sidof.parse(load('mx_sidof'),dict(codNota=1,fecha='20-11-2026',titulo='SYNTHETIC publication',codOrgaDos='CFE'),NOW.isoformat())
    sidof.apply_detail(r,dict(cadenaContenido='COMISION FEDERAL DE ELECTRICIDAD AVISO MULTIPLE DE ADJUDICACION DE CONTRATOS. Línea de transmisión. El concursante adjudicado fue Synthetic Supplier.'),sidof.API+'/nota/1',NOW.isoformat())
    assert decide(r,'mx_sidof',now=NOW)['state']=='closed_or_unavailable'
