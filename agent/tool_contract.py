"""Pre-LLM orchestration design. Tools are registered explicitly, no free web access."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

ToolName = Literal['inspect_tender', 'inspect_document', 'search_official_contact',
                   'verify_status_and_deadline']


@dataclass
class AgentState:
    record_id: str
    source_id: str
    budget_actions: int = 8
    budget_documents: int = 3
    budget_tokens: int = 12000
    steps_used: int = 0
    documents_used: int = 0
    tokens_used: int = 0
    pending_fields: list[str] = field(default_factory=list)
    evidence_urls: list[str] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)

    def can_call(self, name: ToolName) -> bool:
        return (self.steps_used < self.budget_actions
                and self.tokens_used < self.budget_tokens
                and (name != 'inspect_document' or self.documents_used < self.budget_documents))


@dataclass(frozen=True)
class ToolResult:
    status: Literal['success', 'partial', 'blocked', 'failed']
    evidence: list[dict]
    facts: dict
    error_code: str | None = None


class AgentTools(Protocol):
    def inspect_tender(self, tender_id: str) -> ToolResult: ...
    def inspect_document(self, tender_id: str, document_url: str) -> ToolResult: ...
    def search_official_contact(self, tender_id: str) -> ToolResult: ...
    def verify_status_and_deadline(self, tender_id: str) -> ToolResult: ...


# Planned orchestrator invariants (not yet an implementation):
# - Validate tool action against existing agent_action.schema.json.
# - Require approved source host and automation_permission=confirmed_permitted.
# - Treat fetched pages/PDFs as untrusted data, never model instructions.
# - Persist checkpoint before/after each tool call; capped retries/backoff.
# - Gate 'active' via deterministic validator, not model output alone.
