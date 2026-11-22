"""Restartable API pagination: raw pages first, all rows cached before relevance screening."""
import hashlib,json
from dataclasses import dataclass
from .runtime import SourceError,allowed,utc,safe_url

@dataclass
class Page:
    rows:list
    next_url:str|None
    total:int|None=None

class Pager:
    def __init__(self,fetch,scope,initial,parse,identity,*,max_pages=None,resume=False,loader=None):
        self.fetch=fetch;self.loader=loader or fetch.json;self.scope=scope;self.initial=initial;self.parse=parse;self.identity=identity;self.max_pages=max_pages;self.resume=resume
        self.complete=False;self.error=None;self.pages=0;self.requests=0;self.total=None;self.rows=[];self.next_url=initial
    def collect(self):
        db=self.fetch.state.db
        db.execute('CREATE TABLE IF NOT EXISTS page_rows(scope TEXT,id TEXT,payload TEXT,observed TEXT,PRIMARY KEY(scope,id))')
        saved=self.fetch.state.checkpoint(self.scope) if self.resume else None
        if saved and not saved.get('complete'):
            self.next_url=saved['next_url'];seen=set(saved.get('seen',[]));first_total=saved.get('total');received=saved.get('received',0)
        else:
            with db:db.execute('DELETE FROM page_rows WHERE scope=?',(self.scope,))
            seen=set();first_total=None;received=0
        try:
            while self.next_url:
                if self.max_pages is not None and self.pages>=self.max_pages:
                    self.error=SourceError('Explicit page budget reached; resume checkpoint preserved','other',True);break
                current=self.next_url;self.requests+=1;obj=self.loader(current);page=self.parse(obj,current)
                if not isinstance(page.rows,list) or not all(isinstance(row,dict) for row in page.rows):raise SourceError('Rows must be an array of objects','parse_error')
                ids=[self.identity(x) for x in page.rows]
                if any(not x for x in ids) or len(set(ids))!=len(ids):raise SourceError('Missing or duplicate source IDs in page','parse_error')
                digest=hashlib.sha256(json.dumps(ids).encode()).hexdigest()
                if ids and digest in seen:raise SourceError('Repeated page detected','parse_error')
                if first_total is None:first_total=page.total
                elif page.total is not None and page.total!=first_total:raise SourceError('Source total changed during scan; restart required','parse_error')
                if not page.rows and page.next_url:raise SourceError('Empty page with nonterminal next link','parse_error')
                observed=utc()
                with db:
                    for row,key in zip(page.rows,ids):
                        old=db.execute('SELECT 1 FROM page_rows WHERE scope=? AND id=?',(self.scope,key)).fetchone()
                        if old:raise SourceError('Repeated record across pages; completeness not proven','parse_error')
                        db.execute('INSERT INTO page_rows VALUES (?,?,?,?)',(self.scope,key,json.dumps(row,ensure_ascii=False),observed))
                seen.add(digest);received+=len(page.rows);self.pages+=1
                if page.next_url and not allowed(page.next_url,self.fetch.config['approved_hosts']):raise SourceError('Unsafe next link','auth_required')
                self.next_url=page.next_url
                self.fetch.state.checkpoint(self.scope,dict(next_url=safe_url(self.next_url) if self.next_url else None,seen=sorted(seen),total=first_total,received=received,complete=False))
            if self.error is None:
                count=db.execute('SELECT count(*) FROM page_rows WHERE scope=?',(self.scope,)).fetchone()[0]
                if first_total is not None and count!=first_total:raise SourceError(f'total_count mismatch: {count} vs {first_total}','parse_error')
                self.complete=True
                self.fetch.state.checkpoint(self.scope,dict(next_url=None,seen=sorted(seen),total=first_total,received=received,complete=True))
        except (SourceError,TypeError,KeyError,ValueError) as e:
            self.error=e if isinstance(e,SourceError) else SourceError('Invalid page schema: '+type(e).__name__,'parse_error')
        self.total=first_total
        self.rows=[(json.loads(row),observed) for row,observed in db.execute('SELECT payload,observed FROM page_rows WHERE scope=? ORDER BY rowid',(self.scope,))]
        return self
