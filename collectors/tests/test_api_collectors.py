import json,os,copy
from pathlib import Path
import httpx,pytest
from collectors.common.runtime import ROOT,SourceError,schema,utc,CollectorState
from collectors.common.config import load
from collectors.common.records import candidate,notice,pdf_contacts
from collectors.brazil import pncp,compras
from collectors.peru import oece
from collectors.usa import sam
from collectors.run import execute

def test_all_canonical_source_configs():
 files=list((ROOT/'collectors/sources').glob('*.json'));assert len(files)>=11
 for p in files:schema(json.loads(p.read_text()),'source_config.schema.json')

@pytest.mark.parametrize('title',['Construction 10 kV overhead line','Reconstrução linha de transmissão','RECONSTRUCCIÓN LÍNEA AÉREA','Aisladores de vidrio','ENERJİ İLETİM HATTI EİH','Reconductoring AC/DC HVDC','สายส่ง 115 kV'])
def test_no_voltage_volume_insulator_requirement(title):assert candidate(title)
@pytest.mark.parametrize('title',['Internet taxa de transmissão de 1 Gbps BGP','Redes isoladas de tecnologia da informação','Consultancy payroll audit','   '])
def test_telecom_and_whitespace_not_candidates(title):assert not candidate(title)

def test_empty_strings_normalized_without_losing_card():
 r=notice(load('br_pncp'),id='x',title='Line construction',buyer=' ',url='https://pncp.gov.br/one',raw={},description='  ')
 assert r['description_original'] is None and r['buyer_original'] is None and not r['contact_hints']

def test_pdf_emails_not_mislabeled_buyer():
 r=notice(load('br_pncp'),id='x',title='Line',buyer=None,url='https://pncp.gov.br/one',raw={})
 pdf_contacts(r,'Bidder supplier@example.test and auditor clerk@example.test','https://pncp.gov.br/one.pdf')
 assert not r['contact_hints'] and len(r['raw_payload']['document_contact_candidates'])==2

def test_compras_url_fields():
 raw=dict(numeroControlePNCP='12345678000190-1-000001/2026',anoCompraPncp=2026,sequencialCompraPncp=1,orgaoEntidadeCnpj='12345678000190',orgaoEntidadeRazaoSocial='Test Buyer',objetoCompra='Rede elétrica',situacaoCompraNomePncp='Divulgada',dataEncerramentoPropostaPncp='2026-12-01T12:00:00')
 r=pncp.parse(load('br_compras'),compras.normalize(raw),utc());assert r['tender_url'].endswith('/12345678000190/compras/2026/1') and r['deadline_text']=='2026-12-01T12:00:00'

def test_oece_non_bid_date_not_promoted():
 raw={'ocid':'ocds-test','compiledRelease':{'tender':{'description':'Línea aérea','tenderPeriod':{'endDate':'2026-11-01T00:00:00-05:00'}},'parties':[{'roles':['buyer'],'contactPoint':{'telephone':'123'}}]}}
 r=oece.parse(load('pe_oece'),raw,utc());assert r['deadline_text'] is None and r['contact_hints'][0]['phone']=='123'

def test_empty_oece_object_is_unverified_not_zero():
 with pytest.raises(SourceError):oece.page({},'https://contratacionesabiertas.oece.gob.pe/api/v1/records')

def test_sam_key_required(monkeypatch):
 monkeypatch.delenv('SAM_API_KEY',raising=False)
 with pytest.raises(SourceError) as e:sam.listing({'start':'2026-11-01','end':'2026-11-22'},None)
 assert e.value.code=='auth_required'

def test_sam_offsets_are_pages():
 p=sam.page({'opportunitiesData':[{'noticeId':'one'}],'totalRecords':2001},sam.API+'?offset=1&limit=1000')
 assert 'offset=2' in p.next_url

@pytest.mark.parametrize('source',['br_pncp','pe_oece'])
def test_integration_canonical_no_email_and_repeat(source,tmp_path):
 row=dict(numeroControlePNCP='12345678000190-1-000001/2026',anoCompra=2026,sequencialCompra=1,orgaoEntidade={'cnpj':'12345678000190','razaoSocial':'Buyer'},objetoCompra='Construction overhead line 10 kV',informacaoComplementar=' ')
 body={'data':[row],'totalRegistros':1,'totalPaginas':1,'numeroPagina':1} if source=='br_pncp' else {'records':[{'ocid':'one','compiledRelease':{'buyer':{'name':'Buyer'},'tender':{'description':'Construcción línea aérea'}}}],'links':{}}
 client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=body)))
 options=dict(start='2026-11-01',end='2026-11-22',modalities=[6],page_size=10,output=str(tmp_path),document_limit=0)
 run,report=execute(source,options,client=client,sleep=lambda n:None)
 assert len(run['tenders'])==report['central_records']==1 and not run['tenders'][0]['contact_hints']
 assert not report['central_errors'] and report['schema_validation']=='passed'
 _,repeat=execute(source,options,client=client,sleep=lambda n:None)
 assert repeat['changes']==dict(new=0,changed=0,unchanged=1)

def test_invalid_api_preserves_last_good_cards(tmp_path):
 st=CollectorState(tmp_path/'state');st.merge_records('pe_oece',[dict(source_tender_id='existing',title_original='Line')]);st.close()
 client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))
 run,report=execute('pe_oece',dict(start='2026-11-01',end='2026-11-22',output=str(tmp_path)),client=client,sleep=lambda n:None)
 assert not run['coverage']['verified_complete'] and run['errors']
 st=CollectorState(tmp_path/'state');assert st.db.execute('select count(*) from collector_records').fetchone()[0]==1;st.close()

@pytest.mark.parametrize('adapter',[pncp,compras,oece,sam])
@pytest.mark.parametrize('body',[[],42,'not an envelope'])
def test_wrong_api_top_level_does_not_crash(adapter,body):
 with pytest.raises(SourceError):adapter.page(body,'https://pncp.gov.br/?pagina=1&offset=0&limit=10')

def test_html_procurement_keeps_generic_email_unconfirmed():
 from collectors.argentina.energia_tenders import page,parse
 url='https://www.argentina.gob.ar/economia/energia/licitacion-amba-i'
 body='<main><h1>Licitación AMBA I</h1>transmisión eléctrica<a href="mailto:office@example.test">Contacto</a><a href="/official.pdf">Circular</a></main>'
 r=parse(load('ar_energia_tenders'),page(body,url).rows[0],utc())
 assert r['contact_hints'][0]['email']=='office@example.test' and r['raw_payload']['contacts'][0]['contact_type']=='generic_corporate'
 assert r['documents'][0]['url']=='https://www.argentina.gob.ar/official.pdf'

def test_synthetic_control_runs_validate_against_canonical_contract():
 from integration.collector_validation import validate_run
 files=list((ROOT/'collectors/fixtures/runs').glob('*.json'))
 for p in files:
  if p.name=='README.json':continue
  run=json.loads(p.read_text());schema(run,'collector_run.schema.json');validate_run(run)
