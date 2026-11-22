from collectors.common.config import load
from collectors.usa import caiso
from collectors.turkey import sbb
from collectors.argentina import energia
from collectors.planning.models import validate
from collectors.planning import world_bank

def test_caiso_name_table_separate_project_type():
 p=caiso.parse(load('us_caiso_plan'),['Name\nExample reconductoring\nDescription\nReconductor overhead line\nType\nReliability\nExpected In\nService Date\n2030\nInterim\nNone'],'https://www.caiso.com/test.pdf')
 assert len(p)==1 and p[0]['planned_completion_text']=='2030';validate(p[0]);assert p[0]['not_an_open_tender']

def test_sbb_project_ids_and_underground_only_excluded():
 t='ENERJİ\nPROJE SAHİBİ:\nTEİAŞ GN.MD.\n2026D00-1234\nExample EİH\n380 kV Enerji İletim Hattı\n2026-2029\n2026D00-5678\nExample Kablosu\n400 kV Kablo\n2026-2029'
 p=sbb.parse(load('tr_sbb_plan'),[t],'https://www.sbb.gov.tr/test.pdf');assert len(p)==1 and p[0]['project_key']=='2026D00-1234'

def test_argentina_no_map_city_labels_as_projects():
 p=energia.parse(load('ar_energia_plan'),['Argentina\nRodeo\nAMBA II + STATCOM\nLínea 500 kV Rodeo - Chaparro\nET Espinillo\nListado de obras'],'https://www.argentina.gob.ar/test.pdf')
 assert len(p)==1 and p[0]['title_original'].startswith('Línea 500')

def test_world_bank_projects_country_and_closed_filter():
 cfg=load('tr_wb_projects')
 assert world_bank.parse(cfg,dict(id='P1',countrycode=['TR'],status='Closed',project_name='Electricity transmission'),'2026-11-22') is None
 r=world_bank.parse(cfg,dict(id='P1',countrycode=['TR'],status='Pipeline',project_name='Electricity transmission',impagency='Grid Company'),'2026-11-22');assert r['company']=='Grid Company';validate(r)

def test_prepared_parsers_with_synthetic_examples():
 from pathlib import Path
 from collectors.thailand import egat
 from collectors.mexico import cenace
 from collectors.brazil import ons
 root=Path(__file__).resolve().parents[1]/'fixtures'
 assert len(egat.parse(load('th_egat_plan'),(root/'egat_tables.html').read_bytes(),'https://www.egat.co.th/home/en/projects-transmission/'))>0
 assert len(cenace.parse(load('mx_cenace_plan'),[(root/'cenace_page_125.txt').read_text()],'https://www.cenace.gob.mx/plan.pdf'))>0
 assert len(ons.parse(load('br_ons_plan'),(root/'ons_summary.html').read_bytes(),'https://www.ons.org.br/summary'))>0

def test_unverified_plan_sources_are_disabled():
 for id in ['th_egat_plan','mx_cenace_plan','br_ons_plan']:
  cfg=load(id);assert not cfg['enabled'] and cfg['automation_permission']=='not_verified'
