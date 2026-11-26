import copy
from agent.portfolio import report_row,summarize


def sample(source='br_pncp'):
 return {'record_id':'tender_'+source,'source':{'source_id':source,'tender_url':'https://pncp.gov.br/notice/123',
   'source_url':'https://pncp.gov.br/consulta'},'project':{'title':'Insulators 150 kV','country_code':'BR'},
   'procurement':{'deadline_date':'2026-12-18','deadline_timezone':'America/Sao_Paulo'},
   'contacts':[],'documents':[],'assessment':{'decision':'pending_verification'}}

def test_contactless_deadline_preserved_and_url():
 row=report_row(sample())
 assert row['contact_needed'] and row['bid_deadline']=='2026-12-18'
 assert row['official_url']=='https://pncp.gov.br/notice/123'
 assert row['route']=='pending_verification'
 assert row['needs_direct_url'] is False
 assert summarize([row])['with_deadline_but_no_email']==1

def test_no_official_url_does_not_silently_get_fabricated():
 t=sample();t['source']['tender_url']=None
 row=report_row(t)
 assert row['needs_direct_url'] is True
 assert row['direct_tender_url'] is None
 assert row['official_url']=='https://pncp.gov.br/consulta'
 assert not row['missing_identity']

def test_pncp_compras_cross_source_identity_group_preserves_both():
 canon={'raw_payload':{'canonical_identity':{'namespace':'pncp','value':'1234-ABC'}},'source_tender_id':'1234-ABC'}
 a=report_row(sample('br_pncp'),canon);b=report_row(sample('br_compras'),canon)
 m=summarize([a,b]);assert m['candidate_rows']==2
 assert m['opportunity_identity_groups']==1
 assert len(m['cross_source_groups'])==1

def test_planned_projects_not_active_even_with_future_completion():
 p={'record_type':'planned_project_proposal','project_key':'proj-1','source_id':'tr_sbb_plan',
    'country_code':'TR','title_original':'2028 OHL extension','official_plan_url':'https://sbb.gov.tr/report',
    'planned_completion_text':'2028','contact_hints':[]}
 row=report_row(p)
 assert row['route']=='planned_projects'
 assert row['bid_deadline'] is None
 assert row['not_an_open_tender']
 assert row['contact_needed']

def test_confirmation_only_when_exact_verified_contact():
 t=sample();t['contacts']=[{'email':'info@example.org','verified':False},
                         {'email':'procurement@example.org','verified':True,'evidence_url':'https://pncp.gov.br/notice/123'}]
 row=report_row(t)
 assert row['contact_verified'] is True
 assert len(row['contacts'])==2
 assert not row['contact_needed']


def test_all_eight_online_country_probe_mappings_are_explicit():
    from scripts.agent_tools_live_country import SOURCES
    assert set(SOURCES)=={'ID','TH','TR','US','MX','BR','AR','PE'}
    assert SOURCES['ID']==('indonesia','pln_batam')
    assert SOURCES['PE']==('raw','pe_oece')
    assert SOURCES['TH']==('current','th_mea')
    assert len({s for _,s in SOURCES.values()})==8


def test_planned_sources_only_have_separate_stage():
    from scripts.agent_planned_live_smoke import PLAN_SOURCES
    assert PLAN_SOURCES=={'US':'us_caiso_plan','TR':'tr_sbb_plan','AR':'ar_energia_plan'}


def test_source_saved_pdf_is_reused_by_exact_sha_and_never_arbitrary_file(tmp_path):
    import hashlib
    from scripts.agent_tools_live_country import saved_document_mapping
    t=sample();t['documents']=[{'url':'https://pncp.gov.br/docs/123.pdf'}]
    data=b'%PDF-1.4\nmock content';sha=hashlib.sha256(data).hexdigest()
    directory=tmp_path/'capture'/'documents';directory.mkdir(parents=True)
    (directory/(sha+'.pdf')).write_bytes(data)
    raw={'raw_payload':{'documents':[{'source_url':t['documents'][0]['url'],'sha256':sha}]}}
    assert saved_document_mapping(t,raw,tmp_path)=={t['documents'][0]['url']:str(directory/(sha+'.pdf'))}
    (directory/(sha+'.pdf')).write_bytes(b'tampered')
    assert saved_document_mapping(t,raw,tmp_path)=={}

def test_portfolio_inventory_can_keep_title_url_date_without_contact():
    t=sample()
    row=report_row(t)
    assert row['title_original']=='Insulators 150 kV'
    assert row['direct_tender_url'].startswith('https://')
    assert row['contact_needed'] and row['bid_deadline']=='2026-12-18'
    assert summarize([row])['candidate_rows']==1
