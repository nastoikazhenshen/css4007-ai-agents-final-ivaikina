"""Conservative, deterministic commercial candidate tags. Does not replace AI review.

The matcher never proves a product order or market fit merely from a line title.
Differentiate 'this project builds an electrical line' from 'this project buys
insulators/fittings' and from 'it definitely uses a specific supplier's products'.
"""
from __future__ import annotations

import re

CATEGORY_PATTERNS = {
    'underground_cable': r'\b(?:underground\s+cable|ugc|sktt|subterr[aá]ne[oa]|kabel\s+bawah\s+tanah)\b',
    'overhead_line': r'\b(?:overhead\s+(?:transmission\s+)?line|ohl|sutet|sutt|sutm|saluran\s+udara|air\s+line)\b',
    'insulators': r'\b(?:insulators?|isolators?|isoladores?|isoladores|isolateurs?|isolator(?:es)?|izolat[oö]r(?:ler)?)\b',
    'line_fittings': r'\b(?:line\s+fittings|transmission\s+line\s+hardware|herrajes\s+(?:el[eé]ctricos|para\s+l[ií]neas)|line\s+accessories)\b',
    'grid_project': r'\b(?:electrical\s+grid|power\s+network|transmission\s+network|grid\s+(?:extension|modernization|upgrade)|substation|gardu\s+induk)\b',
}


def categories(title: str | None, description: str | None) -> list[str]:
    text = f'{title or ""} {description or ""}'.lower()
    result = [cat for cat, pat in CATEGORY_PATTERNS.items() if re.search(pat, text, re.I)]
    return result


def initial_assessment(title: str | None, description: str | None) -> tuple[list[str], str, str, str]:
    """Return (categories, commercial_relevance, product_demand, line_project_flag).

    Lexical matches mark a candidate 'potential', never 'confirmed relevant'.
    Product demand stays unknown unless independently verified from evidence.
    """
    cats = categories(title, description)
    flag = ('yes' if any(c in cats for c in ('overhead_line', 'underground_cable'))
            else 'no' if any(c in cats for c in ('insulators', 'line_fittings'))
            else 'uncertain')
    return cats, ('potential' if cats else 'uncertain'), 'unknown', flag


def confirmed_category_evidence(record: dict) -> bool:
    paths = {e.get('field_path') for e in record['evidence'] if e.get('source_url') and e.get('excerpt')}
    return ('assessment.commercial_relevance' in paths and
            'assessment.project_categories' in paths)
