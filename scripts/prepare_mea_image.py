#!/usr/bin/env python3
"""Read only the publicly rendered email image on an official MEA tender detail.

Never bypasses access controls or guesses an email when image retrieval fails.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

HOST = "procurement.mea.or.th"


def fetch_email_image(detail: str, output: Path) -> dict:
    report = {"detail_url": detail, "status": "not_found", "image_url": None, "file": None}
    url = urlsplit(detail)
    if url.scheme != 'https' or url.hostname != HOST or not re.fullmatch(r'/Procurement/Detail/[0-9a-f\-]{36}', url.path):
        report['status'] = 'blocked_unapproved_detail';return report
    try:
        from lxml import html
        with urlopen(Request(detail, headers={'User-Agent':'public_research-TenderScout-Research/1.0'}),timeout=20) as r:
            if r.geturl().split('/')[2] != HOST:raise ValueError('redirected_from_official_host')
            content=r.read(2_000_000)
        dom=html.fromstring(content)
        imgs=[]
        for row in dom.xpath('//tr'):
            flat=' '.join(row.text_content().split()).lower()
            if 'email' in flat or 'e-mail' in flat or 'อีเมล' in flat:
                imgs.extend(row.xpath('.//img[@src]'))
        # Never use a site logo, captcha or unrelated image as an "email" sample.
        for img in imgs:
            ref=img.get('src') or ''
            candidate=urljoin(detail,ref)
            parsed=urlsplit(candidate)
            if parsed.scheme!='https' or parsed.hostname != HOST:continue
            with urlopen(Request(candidate,headers={'User-Agent':'public_research-TenderScout-Research/1.0','Referer':detail}),timeout=20) as r:
                if urlsplit(r.geturl()).hostname!=HOST:raise ValueError('image_redirected_from_official_host')
                mime=(r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
                body=r.read(2_000_001)
            if len(body)>2_000_000 or not body:continue
            if mime not in {'image/png','image/jpeg','image/webp'}:continue
            extension={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}[mime]
            output.parent.mkdir(parents=True,exist_ok=True)
            output=output.with_suffix(extension)
            output.write_bytes(body)
            report.update(status='fetched',image_url=candidate,file=str(output),media_type=mime,size_bytes=len(body))
            return report
    except (OSError,ValueError,URLError,HTTPError) as exc:
        report['status']='unavailable'; report['error_type']=type(exc).__name__
    except ImportError:
        report['status']='missing_parser_dependency'
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--detail-url',required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=fetch_email_image(args.detail_url,args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['status']=='fetched' else 2)
