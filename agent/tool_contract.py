"""Shared typed AI Tool contracts. Existing 1.0.0 agent_action JSON remains unchanged.

The typed registry is broader than agent_action's historical four action names;
LEGACY_ACTION maps them to existing action enum until a versioned schema is
explicitly approved. Never invent authority to modify canonical Active routing.
"""
from __future__ import annotations
from dataclasses import dataclass,field
from typing import Literal,Protocol

ToolName=Literal['inspect_tender','inspect_document','extract_image_text',
                 'translate_and_extract','verify_status_and_deadline',
                 'search_official_contact','assess_commercial_relevance']

@dataclass
class AgentState:
    record_id: str
    source_id: str
    budget_actions: int=8
    budget_documents: int=3
    budget_tokens: int=12000
    steps_used: int=0
    documents_used: int=0
    tokens_used: int=0
    pending_fields: list[str]=field(default_factory=list)
    evidence_urls: list[str]=field(default_factory=list)
    failure_reasons: list[str]=field(default_factory=list)

    def can_call(self,name:ToolName)->bool:
        return (self.steps_used<self.budget_actions
                and self.tokens_used<self.budget_tokens
                and (name not in ('inspect_document','translate_and_extract')
                     or self.documents_used<self.budget_documents))

@dataclass(frozen=True)
class ToolResult:
    status: Literal['success','partial','blocked','failed']
    evidence: list[dict]
    facts: dict
    error_code: str|None=None

class AgentTools(Protocol):
    def inspect_tender(self)->ToolResult: ...
    def inspect_document(self,document_url:str)->ToolResult: ...
    def extract_image_text(self,image_id:str)->tuple[ToolResult,dict|None]: ...
    def translate_and_extract(self,document_url:str)->ToolResult: ...
    def verify_status_and_deadline(self)->ToolResult: ...
    def search_official_contact(self)->ToolResult: ...
    def assess_commercial_relevance(self)->ToolResult: ...

# Invariants: source allowlist and robots are enforced in Python; saved PDFs are
# untrusted; unknown contacts are retained; no tool decides Active by itself.
