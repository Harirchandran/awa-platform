"""
Tenant knowledge orchestration.
This layer decides how runtime knowledge should be used without exposing
technical retrieval details to tenant admins or customers.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from backend.ai.rag_service import rag_service
from backend.ai.security import sanitize_untrusted_text
from backend.core.context import ExecutionContext
from backend.ops.logger import logger
NO_MATCHED_ESCALATION_KNOWLEDGE = "No reviewed escalation knowledge matched this message."
@dataclass
class KnowledgeContextResult:
    context_text: str
    sources_used: List[str] = field(default_factory=list)
    mode: str = "not_searchable"
    warnings: List[str] = field(default_factory=list)
    @property
    def has_context(self) -> bool:
        return bool(self.context_text.strip()) and self.context_text != NO_MATCHED_ESCALATION_KNOWLEDGE
class KnowledgeOrchestrator:
    """Coordinates direct tenant knowledge and saved searchable knowledge."""
    DIRECT_FAQ_LIMIT = 15
    FILTERED_FAQ_LIMIT = 75
    async def build_saved_knowledge_context(
        self,
        context: ExecutionContext,
        message: str,
        limit: int = 3,
    ) -> KnowledgeContextResult:
        if not message.strip():
            return KnowledgeContextResult(
                context_text=NO_MATCHED_ESCALATION_KNOWLEDGE,
                mode="empty_query",
            )
        try:
            context_text = await rag_service.search_knowledge_base(context.tenant.id, message, limit=limit)
        except Exception as exc:
            logger.warning(f"Reviewed knowledge lookup failed: {exc}")
            return KnowledgeContextResult(
                context_text=NO_MATCHED_ESCALATION_KNOWLEDGE,
                mode="saved_knowledge_search_failed",
                warnings=[str(exc)],
            )
        if not rag_service.has_helpful_context(context_text):
            return KnowledgeContextResult(
                context_text=NO_MATCHED_ESCALATION_KNOWLEDGE,
                mode="saved_knowledge_no_match",
            )
        return KnowledgeContextResult(
            context_text=context_text,
            sources_used=["tenant_knowledge_base"],
            mode="saved_knowledge_search",
        )
    async def get_saved_knowledge_answer(self, context: ExecutionContext, message: str) -> Optional[str]:
        result = await self.build_saved_knowledge_context(context, message, limit=1)
        if not result.has_context:
            return None
        answer_match = re.search(r"Tenant answer:\s*(.+)", result.context_text, flags=re.IGNORECASE | re.DOTALL)
        if answer_match:
            answer = answer_match.group(1).strip()
        else:
            answer = result.context_text.strip()
        return answer[:1000] if answer else None
    def match_configured_faq(self, message: str, faqs: List[Dict[str, Any]]) -> Optional[str]:
        """Match a user message against configured FAQ questions by word overlap."""
        msg_words = set(w.strip("?.,!'\"") for w in message.lower().split() if len(w) > 2)
        best_match = None
        best_score = 0
        for faq in faqs:
            q = str(faq.get("question", "")).lower()
            q_words = set(w.strip("?.,!'\"") for w in q.split() if len(w) > 2)
            overlap = len(msg_words & q_words)
            threshold = min(2, len(q_words))
            if overlap >= threshold and overlap > best_score:
                best_score = overlap
                best_match = faq.get("answer")
        return str(best_match) if best_match else None
    def match_broad_configured_faq(self, message: str, faqs: List[Dict[str, Any]]) -> Optional[str]:
        msg_words = set(w.strip("?.,!") for w in message.lower().split() if len(w) > 2)
        for faq in faqs:
            q = str(faq.get("question", "")).lower()
            q_words = set(w for w in q.split() if len(w) > 2)
            if len(q_words & msg_words) >= 1:
                answer = faq.get("answer")
                return str(answer) if answer else None
        return None
    def get_direct_faq_context(self, config: Dict[str, Any]) -> KnowledgeContextResult:
        faqs = config.get("faqs", []) or []
        if not faqs:
            return KnowledgeContextResult(
                context_text="No FAQs configured.",
                mode="direct_context_empty",
            )
        lines = []
        for faq in faqs[:self.DIRECT_FAQ_LIMIT]:
            question = sanitize_untrusted_text(str(faq.get("question", "")).strip(), max_chars=300)
            answer = sanitize_untrusted_text(str(faq.get("answer", "")).strip(), max_chars=1200)
            if not question or not answer:
                continue
            lines.append(f"- Q: {question}\n  A: {answer}")
        return KnowledgeContextResult(
            context_text="\n".join(lines) if lines else "No FAQs configured.",
            sources_used=["tenant_config.faqs"],
            mode="direct_context",
        )
knowledge_orchestrator = KnowledgeOrchestrator()