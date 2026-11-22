"""Canonical source configurations with scoped, primary-source permission evidence."""
import json
from .runtime import ROOT,schema

def load(source):
    value=json.loads((ROOT/'collectors/sources'/f'{source}.json').read_text())
    schema(value,'source_config.schema.json')
    return value
