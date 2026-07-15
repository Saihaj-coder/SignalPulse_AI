"""Agentic RAG chatbot for SignalPulse AI (LangGraph + retrieval tools).

The agent:
  1. Reads the user question
  2. Chooses one or more tools: ``vector_search``, ``fulltext_search``, ``graph_search``
  3. Reads the returned evidence (passages + source URLs)
  4. Answers **only** from that evidence, with mandatory citations
  5. If evidence is empty / too weak, refuses instead of guessing

Built with LangGraph's ``create_react_agent`` (ReAct: Reason + Act loop).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool

from signalpulse import retrieval as R
from signalpulse.llm import available_providers, get_chat_model, is_transient_llm_error

REFUSE_PHRASE = "This is not covered in the current sources."

# Common public-sector tokens we pre-retrieve for definition / expansion questions.
_KNOWN_ACRONYMS = {
    "CMS",
    "ONC",
    "NIST",
    "CISA",
    "NVD",
    "HHS",
    "NASCIO",
    "FDA",
    "KEV",
    "BOD",
    "CSF",
    "RMF",
    "OSCAL",
}


# ---------------------------------------------------------------------------
# Tools (thin wrappers the LLM can call)
# ---------------------------------------------------------------------------


@tool
def vector_search(query: str, domain: str = "") -> str:
    """Semantic search over document passages by meaning (cosine similarity).

    Use for conceptual / 'how' / 'what is' questions.
    Leave domain empty unless the user clearly restricts to one area.
    Domain values if needed: 'Cybersecurity & Defense', 'Health IT & Civilian',
    'Tech Standards & Safety', 'State & Local Gov'.
    Note: NIST CSF / RMF / 800-53 live under 'Tech Standards & Safety'.
    """
    hits = R.vector_search(
        query,
        domain=domain or None,
        threshold=0.50,  # slightly loose for a small demo corpus
    )
    if not hits:
        return "NO_EVIDENCE: vector_search returned no passages above the similarity threshold."
    return R.format_evidence(hits)


@tool
def fulltext_search(query: str, domain: str = "") -> str:
    """Keyword / identifier search (CVE ids, docket numbers, exact phrases).

    Use when the question contains a specific id or uncommon proper noun.
    Leave domain empty unless the user clearly restricts to one area.
    """
    hits = R.fulltext_search(query, domain=domain or None)
    if not hits:
        return "NO_EVIDENCE: fulltext_search found no matching passages or documents."
    return R.format_evidence(hits)


@tool
def graph_search(entity_name: str) -> str:
    """Look up an entity in the knowledge graph and its relationships.

    Use for 'related to', 'connected to', 'issued by', 'affects' style
    questions. Returns RELATED_TO triples plus provenance passages with URLs.
    """
    hits = R.graph_search(entity_name)
    if not hits:
        return f"NO_EVIDENCE: graph_search found no entity matching {entity_name!r}."
    return "\n\n".join(h.preview() for h in hits)


TOOLS = [vector_search, fulltext_search, graph_search]


SYSTEM_PROMPT = """You are SignalPulse AI, an assistant for U.S. public-sector \
cybersecurity and regulatory intelligence.

Your job is to answer employee questions using ONLY evidence retrieved from our \
knowledge base (ingested government sources), with citations.

You have three retrieval tools:
- vector_search: semantic / conceptual questions (often your first choice)
- fulltext_search: exact ids (CVE-..., docket numbers) and keywords
- graph_search: entity relationships and provenance (use when the question is \
about what something is connected to / issued / affects / requires)

Rules (non-negotiable):
1. ALWAYS call at least one tool before answering a factual question — unless the \
user message already includes a "Pre-retrieved evidence" block that fully answers it. \
Never answer from memory alone.
2. Base every claim ONLY on tool results or the provided pre-retrieved evidence.
3. If ANY evidence clearly addresses the question (same CVE id, product, framework, \
or agency topic), you MUST answer from that evidence with citations. Do NOT refuse \
just because a later tool call returned unrelated rows.
4. Reply exactly with this sentence and nothing else when evidence is missing or \
irrelevant: "This is not covered in the current sources."
5. Citation style: write short bullets. After each factual bullet, put the source \
URL in parentheses. Do not invent titles or URLs. Do not write \
"(not found in the search results)".
6. Prefer the most specific tool: fulltext for ids, graph for relationships, vector for concepts. \
For a CVE/docket id, call fulltext_search once with that exact id; do not broaden the query \
after you already have matching evidence.
7. Be concise and professional.
8. If evidence is partial, say what is known and what is not — with citations only for \
what is known.
9. For acronym / "what does X stand for" questions: use pre-retrieved evidence when \
present; otherwise call fulltext_search separately for each acronym. Prefer passages \
that explicitly expand the acronym. Never invent an expansion that never appears in \
evidence text.
10. For broad "recent / latest / updates / what's new" questions about an agency or \
topic (e.g. NIST): if retrieved passages are from that agency/topic, summarize the \
most update-oriented or dated items with citations. Do not refuse merely because the \
question is open-ended.
"""


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _agent_for(provider: str):
    """Build (and cache) a ReAct agent bound to one concrete chat model."""
    from langgraph.prebuilt import create_react_agent

    llm = get_chat_model(provider, temperature=0.0)
    return create_react_agent(llm, TOOLS, state_modifier=SYSTEM_PROMPT)


def get_agent():
    """Build the agent on the current primary provider (for notebooks / debugging)."""
    providers = available_providers()
    if not providers:
        raise RuntimeError("No LLM provider configured.")
    return _agent_for(providers[0])


@dataclass
class AgentReply:
    """Structured result of one ask() call (handy for notebooks / the UI)."""

    question: str
    answer: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    messages: list[BaseMessage] = field(default_factory=list, repr=False)
    provider: str | None = None

    @property
    def refused(self) -> bool:
        return "not covered in the current sources" in self.answer.lower()


def _extract_acronyms(question: str) -> list[str]:
    found: list[str] = []
    upper = {a.upper() for a in re.findall(r"\b[A-Za-z]{2,7}\b", question)}
    for acronym in _KNOWN_ACRONYMS:
        if acronym in upper and acronym not in found:
            found.append(acronym)
    # CVE / BOD style ids
    for cve in re.findall(r"\bCVE-\d{4}-\d+\b", question, flags=re.I):
        token = cve.upper()
        if token not in found:
            found.append(token)
    return found


def _looks_like_definition_question(question: str) -> bool:
    q = question.lower()
    triggers = (
        "full form",
        "stands for",
        "stand for",
        "what is",
        "what's",
        "what are",
        "who is",
        "meaning of",
        "expand",
        "acronym",
    )
    return any(t in q for t in triggers)


def _build_user_message(question: str) -> HumanMessage:
    """Optionally pre-retrieve acronym evidence so multi-term questions stay clean."""
    acronyms = _extract_acronyms(question)
    if not acronyms:
        return HumanMessage(content=question)
    if not (_looks_like_definition_question(question) or len(acronyms) >= 2):
        return HumanMessage(content=question)

    packs: list[str] = []
    for acronym in acronyms[:6]:
        hits = R.fulltext_search(acronym, top_k=3)
        if not hits:
            continue
        # Prefer glossary-like hits first (explicit "X — Full Name")
        ranked = sorted(
            hits,
            key=lambda h: (
                0 if "—" in (h.text or "") or " - " in (h.text or "") else 1,
                -h.score,
            ),
        )
        packs.append(f"### {acronym}\n{R.format_evidence(ranked[:2])}")

    if not packs:
        hint = (
            f"{question}\n\n"
            f"(Hint: call fulltext_search separately for each of: {', '.join(acronyms)}.)"
        )
        return HumanMessage(content=hint)

    body = (
        f"{question}\n\n"
        "Pre-retrieved evidence for terms in the question. "
        "Answer from this evidence with citations (URLs in parentheses). "
        "Call tools only for terms still missing.\n\n"
        + "\n\n".join(packs)
    )
    return HumanMessage(content=body)


def _extract_tool_calls(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                calls.append(
                    {"name": tc.get("name"), "args": tc.get("args") or {}}
                )
    return calls


def _final_answer(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.content and not m.tool_calls:
            content = m.content
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, str):
                        parts.append(block)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                return "\n".join(parts).strip()
            return str(content).strip()
    return ""


def _tool_evidence_texts(messages: list[BaseMessage]) -> list[str]:
    from langchain_core.messages import ToolMessage

    out: list[str] = []
    for m in messages:
        if isinstance(m, ToolMessage) and m.content:
            text = str(m.content)
            if text.startswith("NO_EVIDENCE"):
                continue
            out.append(text)
    return out


def _preretrieved_in_user_message(messages: list[BaseMessage]) -> list[str]:
    for m in messages:
        if isinstance(m, HumanMessage):
            text = str(m.content)
            if "Pre-retrieved evidence" in text and "cite:" in text.lower():
                return [text]
    return []


_STOPWORDS = {
    "what",
    "whats",
    "what's",
    "which",
    "where",
    "when",
    "who",
    "whom",
    "whose",
    "why",
    "how",
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "these",
    "those",
    "have",
    "has",
    "had",
    "does",
    "did",
    "are",
    "was",
    "were",
    "been",
    "being",
    "into",
    "about",
    "your",
    "our",
    "sources",
    "listed",
    "full",
    "forms",
    "form",
    "please",
    "tell",
    "give",
    # Vague / news-style question words — do not require them to appear in evidence.
    "recent",
    "latest",
    "update",
    "updates",
    "news",
    "current",
    "overview",
    "summary",
    "summarize",
    "anything",
    "something",
    "information",
    "details",
    "happen",
    "happening",
    "there",
    "some",
    "into",
}

# Topic/agency tokens that strongly link a question to retrieved corpus material.
_TOPIC_MARKERS = (
    "nist",
    "cisa",
    "nvd",
    "cms",
    "onc",
    "hhs",
    "healthit",
    "nascio",
    "kev",
    "dod",
    "rmf",
)


def _evidence_likely_relevant(question: str, evidence_blobs: list[str]) -> bool:
    """True if tool/pre-retrieve output looks usable for this question."""
    if not evidence_blobs:
        return False
    blob = "\n".join(evidence_blobs).lower()
    q = question.lower()
    has_cite = "cite:" in blob or "http" in blob
    if not has_cite:
        return False

    ids = re.findall(r"cve-\d{4}-\d+", q) + re.findall(r"\bbod\s*\d+", q)
    if ids:
        return any(i.lower().replace(" ", "") in blob.replace(" ", "") for i in ids)
    acronyms = _extract_acronyms(question)
    if acronyms and (_looks_like_definition_question(question) or len(acronyms) >= 2):
        return any(a.lower() in blob for a in acronyms)

    # Broad agency/topic questions ("recent NIST updates"): topic + retrieved cites.
    topics = [t for t in _TOPIC_MARKERS if t in q]
    if topics and any(t in blob for t in topics):
        scores = [float(m) for m in re.findall(r"score=([0-9]+\.?[0-9]*)", blob)]
        if not scores or max(scores) >= 0.72:
            return True

    # General questions: require meaningful query-term overlap with evidence text.
    words = [
        w
        for w in re.findall(r"[a-z]{4,}", q)
        if w not in _STOPWORDS
    ]
    if not words:
        return True
    overlap = sum(1 for w in words if w in blob)
    need = 1 if len(words) <= 2 else max(2, (len(words) + 2) // 3)
    return overlap >= need


def _answer_is_coverage_hedge(answer: str) -> bool:
    """True when the model hedges 'not in evidence' instead of refusing cleanly."""
    low = (answer or "").lower()
    hedges = (
        "not explicitly mentioned",
        "does not contain information",
        "do not provide information",
        "does not provide information",
        "not mentioned in the",
        "not found in the",
        "no information about",
        "not available in the",
        "cannot find",
        "could not find",
    )
    return sum(1 for h in hedges if h in low) >= 1


def _all_tool_results_empty(messages: list[BaseMessage]) -> bool:
    from langchain_core.messages import ToolMessage

    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    if not tool_msgs:
        return False
    return all(str(m.content).startswith("NO_EVIDENCE") for m in tool_msgs)


def _sanitize_answer(answer: str) -> str:
    """Remove messy hedge phrases models sometimes append."""
    lines = []
    for line in (answer or "").splitlines():
        low = line.lower()
        if "not found in the search results" in low:
            continue
        if "not found in the retrieved" in low:
            continue
        lines.append(line)
    text = "\n".join(lines).strip()
    if text.lower().count("not covered in the current sources") > 1:
        text = REFUSE_PHRASE
    return text


def _enforce_refuse_if_weak(
    question: str,
    answer: str,
    messages: list[BaseMessage],
) -> str:
    """Force the fixed refuse phrase when evidence truly isn't there."""
    answer = _sanitize_answer(answer)
    evidence = _tool_evidence_texts(messages) + _preretrieved_in_user_message(messages)
    has_evidence = _evidence_likely_relevant(question, evidence)

    if not has_evidence:
        # Do not wipe a grounded answer that already cites URLs.
        if "http" in answer.lower() and "not covered in the current sources" not in answer.lower():
            return answer
        return REFUSE_PHRASE

    # Model sometimes hedges with unrelated cites; treat pure hedges as refuse.
    if _answer_is_coverage_hedge(answer):
        content_lines = [
            ln
            for ln in answer.splitlines()
            if ln.strip()
            and not ln.strip().startswith("#")
        ]
        hedge_n = sum(
            1
            for ln in content_lines
            if any(
                h in ln.lower()
                for h in (
                    "not explicitly mentioned",
                    "does not contain information",
                    "do not provide information",
                    "does not provide information",
                    "not mentioned in the",
                    "not found in the",
                    "no information about",
                )
            )
        )
        if content_lines and hedge_n >= len(content_lines):
            return REFUSE_PHRASE

    if "not covered in the current sources" in answer.lower() and len(answer) > 80:
        cleaned = "\n".join(
            ln
            for ln in answer.splitlines()
            if "not covered in the current sources" not in ln.lower()
        ).strip()
        return cleaned or answer
    return answer


def ask(question: str) -> AgentReply:
    """Run one question through the agentic RAG loop.

    Tries each configured provider in order. On rate-limit / quota errors,
    automatically falls through to the next (Mistral / DeepSeek / Gemini, etc.).
    """
    providers = available_providers()
    if not providers:
        raise RuntimeError("No LLM provider configured.")

    # Prompt may change during a session; never serve a stale agent graph.
    _agent_for.cache_clear()
    user_msg = _build_user_message(question.strip())

    errors: list[str] = []
    for provider in providers:
        try:
            agent = _agent_for(provider)
            result = agent.invoke({"messages": [user_msg]})
            messages: list[BaseMessage] = result["messages"]
            answer = _final_answer(messages)
            tool_calls = _extract_tool_calls(messages)

            # Recovery: model sometimes refuses after a noisier follow-up search
            # even though earlier evidence already matched.
            evidence = _tool_evidence_texts(messages) + _preretrieved_in_user_message(
                messages
            )
            if (
                "not covered in the current sources" in answer.lower()
                and _evidence_likely_relevant(question, evidence)
            ):
                recovery = HumanMessage(
                    content=(
                        "Relevant evidence was already provided above. "
                        "Answer the original question using ONLY that evidence. "
                        "Use short bullets with URLs in parentheses. Do not refuse."
                    )
                )
                result2 = agent.invoke({"messages": messages + [recovery]})
                messages = result2["messages"]
                answer = _final_answer(messages)
                tool_calls = _extract_tool_calls(messages)

            answer = _enforce_refuse_if_weak(question, answer, messages)

            return AgentReply(
                question=question,
                answer=answer,
                tool_calls=tool_calls,
                messages=messages,
                provider=provider,
            )
        except Exception as exc:  # noqa: BLE001 - try next free-tier provider
            if is_transient_llm_error(exc) and provider != providers[-1]:
                errors.append(f"{provider}: {type(exc).__name__}")
                continue
            if errors:
                raise RuntimeError(
                    f"All LLM providers failed after {[e for e in errors]}; "
                    f"last error from {provider}: {type(exc).__name__}: {exc}"
                ) from exc
            raise
    raise RuntimeError("No LLM provider succeeded.")
