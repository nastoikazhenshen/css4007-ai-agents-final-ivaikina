"""Documented PNCP line-item discovery, independent of submission-window proof."""
from urllib.parse import parse_qs, urlencode, urlsplit

from collectors.common.pagination import Page, Pager
from collectors.common.relevance import assess, plain
from collectors.common.runtime import SourceError, fingerprint, utc
from .pncp_legacy import attachments_url


def page(obj, url):
    query = parse_qs(urlsplit(url).query)
    number = int(query['pagina'][0])
    size = int(query['tamanhoPagina'][0])
    total = None
    if obj is None:
        return Page([], None)
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict) and isinstance(obj.get('itens'), list):
        rows = obj['itens']
        total = obj.get('totalRegistros')
        if total is not None and (type(total) is not int or total < 0):
            raise SourceError('Invalid PNCP item total', 'parse_error')
        if 'numeroPagina' in obj and obj['numeroPagina'] != number:
            raise SourceError('PNCP item page number mismatch', 'parse_error')
    else:
        raise SourceError('PNCP items must be an array or documented itens envelope', 'parse_error')
    if len(rows) > size:
        raise SourceError('PNCP item response exceeds requested page size', 'parse_error')
    more = number * size < total if total is not None else len(rows) == size
    query = {k: v[0] for k, v in query.items()}
    query['pagina'] = number + 1
    return Page(rows, url.split('?')[0] + '?' + urlencode(query) if more else None, total)


def identity(row):
    value = row.get('numeroItem')
    return str(value) if value is not None and not isinstance(value, (dict, list, bool)) else ''


def collect(fetch, record, *, max_pages=None, page_size=100, resume=False):
    """Persist all received items before topic assessment; partial pages remain usable leads."""
    if not 1 <= page_size <= 500:
        raise ValueError('Item page size must be 1..500')
    source_row = record['raw_payload']['source_record']
    initial = attachments_url(source_row, 'itens') + '?' + urlencode(
        {'pagina': 1, 'tamanhoPagina': page_size})
    scope = fetch.config['source_id'] + ':items:' + fingerprint(initial)
    scan = Pager(fetch, scope, initial, page, identity, max_pages=max_pages,
                 resume=resume).collect()
    result = dict(source_url=initial, checked_at=utc(), pages_received=scan.pages,
                  pages_requested=scan.requests, total_reported=scan.total,
                  received=len(scan.rows), complete=scan.complete,
                  next_url=scan.next_url, items=[row for row, _ in scan.rows],
                  error=dict(code=scan.error.code, reason=str(scan.error)) if scan.error else None)
    record['raw_payload']['line_items'] = result
    evidence = []
    texts = []
    for row, observed in scan.rows:
        text = '\n'.join(plain(row.get(field)) for field in
                         ['descricao', 'informacaoComplementar', 'ncmNbsDescricao'] if row.get(field))
        texts.append(text)
        verdict = assess(text)
        if verdict['verdict'] != 'excluded':
            evidence.append(dict(source_url=initial, item_number=row['numeroItem'],
                                 locator='numeroItem=' + str(row['numeroItem']),
                                 original_excerpt=text, observed_at=observed, scope=verdict))
    # Full original rows remain in line_items and SQLite, including nonmatching items.
    record['raw_payload']['line_item_scope_evidence'] = evidence
    if evidence:
        record['raw_payload']['description_before_line_items'] = record.get('description_original')
        record['description_original'] = '\n'.join(
            [record.get('description_original') or '', *[e['original_excerpt'] for e in evidence]])
    return result
