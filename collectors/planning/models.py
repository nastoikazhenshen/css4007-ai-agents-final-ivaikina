"""Local PlannedProject proposal ONLY. Not a central TenderRecord or CollectorRun."""
import hashlib,re
from collectors.common.runtime import utc

def project(config,title,url,excerpt,*,key=None,buyer=None,locator=None,completion=None,status=None):
    assert title and url.startswith('https://')
    return dict(record_type='planned_project_proposal',interface_version='draft-0.1',project_key=key or hashlib.sha256((url+title).encode()).hexdigest()[:24],country_code=config['country_code'],source_id=config['source_id'],title_original=title,official_plan_url=url,company=buyer,contact_hints=[],planned_completion_text=completion,source_status_text=status,tender_url=None,bid_deadline_at_utc=None,not_an_open_tender=True,ohl_relevance='potential; source scope requires review',documents=[],evidence=[dict(source_url=url,locator=locator or 'official source',original_excerpt=excerpt,observed_at=utc())],fetched_at=utc())

def validate(value):
    required={'record_type','project_key','country_code','source_id','title_original','official_plan_url','not_an_open_tender','evidence'}
    if not required<=value.keys() or value['record_type']!='planned_project_proposal' or not value['not_an_open_tender'] or value.get('tender_url') or value.get('bid_deadline_at_utc'):raise ValueError('Invalid local future-project proposal')
