"""Read-only connector for the official Brazilian PNCP."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

ORIGIN = "https://pncp.gov.br"
PUBLICATION = ORIGIN + "/api/consulta/v1/contratacoes/publicacao"
OPEN_PROPOSALS = ORIGIN + "/api/consulta/v1/contratacoes/proposta"
DOCS = ORIGIN + "/api/consulta/swagger-ui/index.html"
RETRYABLE = {408, 429, 500, 502, 503, 504}


def get_json(client: httpx.Client, url: str, **params: Any) -> Any:
    for attempt in range(3):
        try:
            response = client.get(url, params=params)
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
            continue
        if response.status_code in RETRYABLE and attempt < 2:
            retry_after = response.headers.get("Retry-After", "")
            delay = int(retry_after) if retry_after.isdigit() else (
                5 * 2**attempt if response.status_code == 429 else 2**attempt)
            if delay <= 30:
                time.sleep(delay)
                continue
        if response.status_code == 204:
            return None
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise ValueError(
                f"HTTP {response.status_code} {response.url}: "
                f"{' '.join(response.text[:300].split())}"
            ) from error
        return response.json()


@dataclass
class Batch:
    modality: int
    records: dict[str, dict] = field(default_factory=dict)
    pages: int = 0
    api_total: int | None = None
    expected_pages: int | None = None
    received_rows: int = 0
    invalid_rows: int = 0
    errors: list[str] = field(default_factory=list)
    complete: bool = False


def collect(
    client: httpx.Client, start: date, end: date, modality: int, size: int,
    max_pages: int | None = None, *, open_only: bool = False,
) -> Batch:
    result = Batch(modality)
    page = 1
    page_ids: set[tuple[str, ...]] = set()
    while True:
        try:
            params = {"dataFinal": end.strftime("%Y%m%d"),
                      "codigoModalidadeContratacao": modality, "pagina": page, "tamanhoPagina": size}
            if not open_only:
                params["dataInicial"] = start.strftime("%Y%m%d")
            payload = get_json(client, OPEN_PROPOSALS if open_only else PUBLICATION, **params)
            if payload is None:
                if page == 1:
                    result.api_total = result.expected_pages = 0
                    result.complete = True
                    return result
                raise ValueError("HTTP 204 antes da última página esperada")
            rows = payload.get("data")
            total_pages = payload.get("totalPaginas")
            total_records = payload.get("totalRegistros")
            if not isinstance(rows, list) or type(total_pages) is not int or type(total_records) is not int:
                raise ValueError("Estrutura de paginação PNCP inválida")
            if payload.get("numeroPagina") != page:
                raise ValueError(f"PNCP retornou página {payload.get('numeroPagina')} para página {page}")
            if total_pages < 0 or total_records < 0 or (not rows and total_pages >= page and total_records):
                raise ValueError("Contagem ou conteúdo de página incompatível")
            if page == 1:
                result.api_total = total_records
                result.expected_pages = total_pages
            keys = tuple(str(row.get("numeroControlePNCP", "")) for row in rows if isinstance(row, dict))
            if keys and keys in page_ids:
                raise ValueError("PNCP repetiu uma página")
            page_ids.add(keys)
            result.pages += 1
            result.received_rows += len(rows)
            for row in rows:
                if not isinstance(row, dict) or not row.get("numeroControlePNCP"):
                    result.invalid_rows += 1
                    continue
                key = row["numeroControlePNCP"]
                previous = result.records.get(key)
                if not previous or version(row) >= version(previous):
                    result.records[key] = row
            if page >= total_pages:
                if (
                    result.invalid_rows or result.received_rows != result.api_total
                    or len(result.records) != result.api_total
                ):
                    result.errors.append("A contagem de identificadores únicos não coincide com a primeira página")
                result.complete = not result.errors
                return result
            if max_pages is not None and page >= max_pages:
                result.errors.append(f"Limite explícito de {max_pages} páginas atingido")
                return result
            if page % 25 == 0:
                print(f"PNCP modality {modality}: {page}/{total_pages} pages, "
                      f"{len(result.records)} unique records", flush=True)
            page += 1
            time.sleep(0.15)
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            result.errors.append(f"Página {page}: {type(exc).__name__}: {exc}")
            return result


def version(row: dict) -> str:
    return row.get("dataAtualizacaoGlobal") or row.get("dataAtualizacao") or ""


def purchase_path(row: dict) -> str:
    cnpj = (row.get("orgaoEntidade") or {}).get("cnpj", "")
    year, seq = row.get("anoCompra"), row.get("sequencialCompra")
    if not (isinstance(cnpj, str) and len(cnpj) == 14 and cnpj.isascii() and cnpj.isdigit()
            and type(year) is int and type(seq) is int and 2000 <= year <= 2100 and seq > 0):
        raise ValueError("PNCP record missing valid CNPJ/year/sequence")
    return f"{cnpj}/{year}/{seq}"


def detail_url(row: dict) -> str:
    cnpj, year, seq = purchase_path(row).split("/")
    return f"{ORIGIN}/api/consulta/v1/orgaos/{cnpj}/compras/{year}/{seq}"


def record_url(row: dict) -> str:
    return f"{ORIGIN}/app/editais/{purchase_path(row)}"


def attachments_url(row: dict, suffix: str) -> str:
    cnpj, year, seq = purchase_path(row).split("/")
    return f"{ORIGIN}/api/pncp/v1/orgaos/{cnpj}/compras/{year}/{seq}/{suffix}"
