"""LLM-based entity & relationship extraction for SignalPulse AI.

This is the one place we use an LLM inside the ingestion pipeline. Cleaning and
chunking (Step 3) are deterministic; here the LLM does something rules cannot:
read a passage of prose and pull out the **entities** (things) and
**relationships** (how they connect) that form the knowledge graph.

Key ideas
---------
* **Structured output via Pydantic** — we don't ask for free text and then parse
  it. We hand the model a Pydantic schema (``Extraction``) and use LangChain's
  ``with_structured_output``, so the provider is constrained (function-calling /
  JSON mode) to return data that validates against that schema. No brittle
  regex, no "sure, here's your JSON:" preambles.
* **Controlled vocabulary** — entity ``type`` is an ``Enum`` and relations are
  guided to a small UPPER_SNAKE_CASE set, so the graph stays consistent instead
  of exploding into thousands of near-duplicate labels.
* **Grounding (anti-hallucination)** — temperature 0, an explicit "only use the
  passage, invent nothing, empty is fine" instruction, and a post-processing
  pass that drops any relationship whose endpoints were not themselves extracted
  as entities.
* **Fallback** — extraction runs on Gemini and automatically fails over to Groq
  (see ``signalpulse.llm``).
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from signalpulse.llm import available_providers, get_chat_model

if TYPE_CHECKING:
    from langchain_core.runnables import Runnable

    from signalpulse.processing import Chunk


# ---------------------------------------------------------------------------
# The schema the LLM must fill in
# ---------------------------------------------------------------------------


class EntityType(str, Enum):
    """Controlled vocabulary of entity categories for our public-sector domain."""

    ORGANIZATION = "Organization"   # agencies, vendors, companies (CISA, Microsoft)
    PRODUCT = "Product"             # software/hardware (Windows, SP Page Builder)
    VULNERABILITY = "Vulnerability" # CVEs, weaknesses
    REGULATION = "Regulation"       # rules, laws, mandates
    STANDARD = "Standard"           # frameworks/standards (NIST CSF, FIPS)
    TECHNOLOGY = "Technology"       # general tech concepts (encryption, cloud)
    PERSON = "Person"
    LOCATION = "Location"
    EVENT = "Event"                 # incidents, deadlines, publications
    OTHER = "Other"


# Map for normalizing whatever the LLM returns back to our controlled vocabulary.
_ALLOWED_TYPES: dict[str, str] = {t.value.lower(): t.value for t in EntityType}


def _normalize_type(value: str) -> str:
    """Coerce an LLM-provided type to the controlled vocabulary (else 'Other')."""
    return _ALLOWED_TYPES.get(str(value).strip().lower(), EntityType.OTHER.value)


class Entity(BaseModel):
    # NOTE: `type` is a plain string (not the Enum) on purpose. Some providers
    # (e.g. Groq) *strictly* reject tool calls whose value falls outside an enum,
    # which would fail the whole extraction if the model invents a category. We
    # accept any string here and normalize it to our vocabulary in `_clean()`.
    name: str = Field(description="The entity's name, copied exactly as it appears in the passage.")
    type: str = Field(
        description=(
            "The single best category for this entity. Choose EXACTLY ONE of: "
            "Organization, Product, Vulnerability, Regulation, Standard, "
            "Technology, Person, Location, Event. Use 'Other' if none fit."
        )
    )


class Relationship(BaseModel):
    source: str = Field(description="Name of the source entity. MUST match one of the extracted entity names.")
    relation: str = Field(
        description=(
            "A short UPPER_SNAKE_CASE label describing how source relates to target. "
            "Prefer these when they fit: AFFECTS, EXPLOITS, MITIGATES, ISSUED_BY, "
            "REQUIRES, APPLIES_TO, PART_OF, RELATED_TO."
        )
    )
    target: str = Field(description="Name of the target entity. MUST match one of the extracted entity names.")


class Extraction(BaseModel):
    """Everything extracted from a single passage."""

    entities: list[Entity] = Field(
        default_factory=list,
        description="All distinct entities explicitly mentioned in the passage. Empty list if none.",
    )
    relationships: list[Relationship] = Field(
        default_factory=list,
        description="Relationships explicitly stated between the extracted entities. Empty list if none.",
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an information-extraction engine for a public-sector \
intelligence system. You read one passage of text and extract a knowledge graph \
of entities and relationships from it.

Strict rules:
1. GROUNDING: Extract ONLY facts explicitly stated in the passage. Never use \
outside knowledge, and never guess or infer facts that are not written.
2. If the passage contains no meaningful entities, return empty lists. An empty \
result is correct and expected for boilerplate or unrelated text.
3. Entity names must be copied from the passage (you may trim surrounding words, \
but do not rephrase or expand acronyms unless the passage does).
4. Choose the single best `type` for each entity from the allowed categories.
5. Create a relationship whenever the passage states a connection between two of \
your extracted entities -- for example a vulnerability in/affecting a product \
(AFFECTS), a rule or patch issued by an organization (ISSUED_BY), or a control \
that mitigates a risk (MITIGATES). Both entities MUST be in your entities list. \
Use short UPPER_SNAKE_CASE relations.
6. Do not duplicate entities or relationships."""

HUMAN_PROMPT = """Document title: {title}

Passage:
\"\"\"
{passage}
\"\"\"

Extract the entities and relationships."""


# ---------------------------------------------------------------------------
# Extractor (structured output + fallback)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_extractor() -> "Runnable":
    """Build the extraction chain: prompt -> structured LLM (with fallback)."""
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages(
        [("system", SYSTEM_PROMPT), ("human", HUMAN_PROMPT)]
    )

    providers = available_providers()
    if not providers:
        raise RuntimeError(
            "No LLM provider configured. Set GOOGLE_API_KEY and/or GROQ_API_KEY in .env"
        )

    chains = [
        prompt | get_chat_model(p, temperature=0.0).with_structured_output(Extraction)
        for p in providers
    ]
    return chains[0].with_fallbacks(chains[1:]) if len(chains) > 1 else chains[0]


# ---------------------------------------------------------------------------
# Post-processing: enforce grounding + de-duplicate
# ---------------------------------------------------------------------------


def _clean(extraction: Extraction) -> Extraction:
    """Drop dangling relationships and de-duplicate entities/relationships."""
    # De-dup entities on (lowercased name, type).
    seen_e: set[tuple[str, str]] = set()
    entities: list[Entity] = []
    for e in extraction.entities:
        name = e.name.strip()
        if not name:
            continue
        etype = _normalize_type(e.type)
        key = (name.lower(), etype)
        if key in seen_e:
            continue
        seen_e.add(key)
        entities.append(Entity(name=name, type=etype))

    valid_names = {e.name.lower() for e in entities}

    # Keep only relationships whose endpoints were extracted as entities.
    seen_r: set[tuple[str, str, str]] = set()
    relationships: list[Relationship] = []
    for r in extraction.relationships:
        src, tgt = r.source.strip(), r.target.strip()
        rel = r.relation.strip().upper().replace(" ", "_")
        if src.lower() not in valid_names or tgt.lower() not in valid_names:
            continue  # dangling -> likely hallucinated; drop it
        if src.lower() == tgt.lower():
            continue
        key = (src.lower(), rel, tgt.lower())
        if key in seen_r:
            continue
        seen_r.add(key)
        relationships.append(Relationship(source=src, relation=rel, target=tgt))

    return Extraction(entities=entities, relationships=relationships)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_from_text(text: str, *, title: str = "") -> Extraction:
    """Extract a grounded, de-duplicated Extraction from a passage of text."""
    if not text or not text.strip():
        return Extraction()
    raw: Extraction = get_extractor().invoke(
        {"title": title or "(untitled)", "passage": text}
    )
    return _clean(raw)


def extract_from_chunk(chunk: "Chunk", *, title: str = "") -> Extraction:
    """Convenience wrapper to extract from a ``Chunk`` object."""
    return extract_from_text(chunk.text, title=title)
