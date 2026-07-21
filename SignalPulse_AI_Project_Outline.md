# SignalPulse AI — Detailed Project Outline

**Public-sector intelligence from official U.S. government sources — collected, indexed, and asked with citations.**

Document type: Full technical outline with fundamentals

---

## How to read this document

This outline is written so that a reader who is **new to Agentic AI and RAG** can follow it end to end. Every technical term is explained the first time it appears, in plain language, before it is used in the design. The document flows in this order:

1. Plain-English overview of what we are building and why.
2. A glossary of every core concept (LLM, embeddings, vector search, RAG, knowledge graph, agents, etc.), each explained from scratch.
3. The full system architecture, component by component, in execution order.
4. A deep description of every tool, library, and technique used, including the basic ones.
5. The data sources and how each is accessed.
6. The step-by-step data flow (ingestion) and query flow (chatbot).
7. Cost model (fully free), roadmap, risks, and success criteria.

---

## 1. Problem statement & executive overview

### Problem

Companies that support U.S. federal and state agencies must constantly track public sources spanning **cybersecurity**, **health IT**, **technology standards**, **federal regulation**, and **state government IT priorities**: CISA/NVD vulnerability alerts, Federal Register / CMS / ONC notices, NIST frameworks and controls, DoD-related publications, and state CIO materials such as NASCIO. Today this is largely **manual** — analysts skim sites and feeds, copy fragments into tickets or emails, and still miss deadlines or connections. That process is slow, hard to verify, and does not scale as client load grows.

Employees also need answers they can **trust and cite**: not a generic chatbot response, but a short answer grounded in the *current* documents, with links back to the original sources.

### Solution — SignalPulse AI

**SignalPulse AI** is an internal **public-sector intelligence** tool — not a generic chatbot and not “RAG alone.” It automatically **collects and refreshes** selected **official public U.S. government** sources across the domains above, indexes them in one place, and lets employees ask questions with answers that stay **faithful to that corpus**.

Concretely, the system has two parts:

1. **A scheduled (or on-demand) data factory (ETL pipeline)** that fetches those sources, cleans and chunks text, embeds passages, extracts entities/relationships with an LLM, and upserts everything into **one Neo4j database** (documents + passages + embeddings + knowledge-graph edges). Runs are **incremental** (skip unchanged content via content hashes). Example source families: CISA KEV/alerts, NVD CVEs, Federal Register / CMS / ONC–HealthIT, NIST publications, and NASCIO materials.
2. **An Ask Assistant (Agentic RAG)** that employees query in plain English. An LLM **chooses** among retrieval tools — semantic (vector) search, keyword/id (fulltext) search, and entity/relationship (graph) lookup — then answers **only from retrieved evidence**, with **mandatory citations** (title + URL). If nothing relevant is found, it refuses instead of guessing from general model knowledge.

Day-to-day value is **monitoring + up-to-date, cited Q&A**. Relationship / cross-domain lookups are a supporting capability when the knowledge graph connects entities.

### What success looks like (primary vs secondary)

| Priority | Capability | Example employee question |
|---|---|---|
| **Primary** | Up-to-date, **cited** Q&A over ingested sources | *"What is CVE-2026-56291, what product does it affect, and what's the remediation deadline?"* |
| **Primary** | Agentic tool choice (not fixed vector-only RAG) | Conceptual → vector; CVE/docket id → fulltext; "tied to / issued / affects" → graph when useful |
| **Secondary** | Relationship / multi-hop lookups when the graph supports them (including cross-domain when entities connect) | *"Are there CISA BOD/KEV remediation obligations tied to the latest KEV entries?"* or, when densely linked, cross-domain risk questions |

### Explicit non-goals

- Not a substitute for legal/compliance counsel or official agency determinations.
- Not sub-minute real-time alerting (ingest is batch/scheduled).
- Not a guarantee that every answer “walks the graph”; most practical questions are solved by vector and/or fulltext retrieval first.
- Not a promise to scrape every government site that blocks automated clients; we prefer official APIs/feeds and documented fallbacks.

### Cost stance

The stack is designed to run at **near-zero software cost**: free-tier LLMs, local embeddings, and Neo4j Community (local/portable).

---

## 2. Core concepts explained from scratch (glossary)

> Read this section once. Everything after it assumes these terms.

### 2.1 LLM (Large Language Model)
A Large Language Model is an AI trained on huge amounts of text that can read a prompt and generate human-like text in response. Examples: Google Gemini, OpenAI GPT, Meta Llama. In this project the LLM does two jobs: (1) during ingestion, it reads a government document and extracts structured facts from it; (2) during chat, it acts as the "brain" that decides what to look up and writes the final answer.

**Prompt**: the instruction text we send to the LLM.
**Token**: the unit an LLM reads/writes — roughly ¾ of a word. Usage limits and pricing are measured in tokens.
**Context window**: the maximum number of tokens an LLM can consider at once (Gemini free tier is very large — up to ~1 million tokens).
**Hallucination**: when an LLM makes up something that sounds correct but is not grounded in real data. A major goal of this project is to prevent this by forcing every answer to come from retrieved source documents.

### 2.2 Embedding
An **embedding** is a list of numbers (a "vector") that represents the *meaning* of a piece of text. Texts with similar meaning get similar vectors, even if they use different words. For example, "car" and "automobile" produce nearby vectors.

We create embeddings using an **embedding model** (separate from the LLM). We embed every chunk of every document once and store the vectors. Later, when a user asks a question, we embed the question and find the stored chunks whose vectors are closest — those are the most relevant pieces of text.

**Cosine similarity**: the math used to measure how "close" two vectors are (a score from -1 to 1; closer to 1 means more similar meaning).

### 2.3 Vector search (semantic search)
Traditional search matches exact keywords. **Vector/semantic search** matches *meaning* using embeddings and cosine similarity. It answers broad, conceptual questions ("what are the recent AI compliance requirements?") even when the document never used those exact words.

### 2.4 Knowledge graph
A **knowledge graph** stores information as **nodes** (things) connected by **relationships** (edges). For example:
`(CVE-2026-56291) —[AFFECTS]→ (Balbooa Forms)` and `(CISA) —[ISSUED_BY]→ (BOD 26-04)`.

Unlike a spreadsheet or plain text, a graph lets you *follow connections*. That supports relationship questions (including cross-domain hops **when shared entities exist**). We use **Neo4j**, a popular graph database, and its query language **Cypher**.

### 2.5 Hybrid graph + vector database
Normally you might use two systems: one for vectors (semantic search) and one for the graph (relationships). Neo4j can do **both** — graph nodes/edges *and* vector + full-text indexes — so documents, passages, embeddings, entities, and citation metadata live in one place. This is the "hybrid" store.

### 2.6 RAG (Retrieval-Augmented Generation)
RAG is a technique where, before the LLM answers, we **retrieve** relevant real documents from our database and feed them into the prompt. The LLM then generates its answer *based on those documents* rather than from memory. This is what keeps answers accurate, current, and grounded — and lets us show citations.

- **Retrieval** = finding the right passages (via vector search, fulltext, and/or graph lookup).
- **Augmented** = we add those passages into the prompt.
- **Generation** = the LLM writes the answer using them.

### 2.7 Agent and Agentic RAG
A basic RAG system always runs the same retrieval (usually vector search), then answers. An **agent** is an LLM that can *decide for itself* which tool to call, whether to search again, and when it has enough evidence.

**Agentic RAG** = RAG where the agent chooses *how* to retrieve (e.g., fulltext for a CVE id, vector for a conceptual NIST question, graph for “what is this entity tied to?”), possibly in multiple rounds, before answering with citations.

**When RAG can be called GraphRAG.** GraphRAG is a style of RAG where retrieval is **graph-first**: the system mainly walks entities and relationships (and sometimes graph community summaries) to assemble context, then the LLM answers from that graph-derived evidence. Using a graph database alone does **not** make a system GraphRAG — the *retrieval strategy* has to center on the graph.

**Why SignalPulse is Agentic RAG, not GraphRAG.** We store documents, passages, embeddings, *and* a knowledge graph in Neo4j (a hybrid store). At query time the agent may use `vector_search`, `fulltext_search`, or `graph_search`. Day-to-day answers usually come from vector and/or fulltext; graph lookup is used when the question is about connections. That means SignalPulse is **hybrid / graph-augmented Agentic RAG** — graph-capable, but not GraphRAG end-to-end.

**Tool (function calling)**: a tool is a normal Python function (e.g., `vector_search(query)`) that we expose to the LLM. The LLM cannot touch the database directly; instead it "calls" these tools by name with arguments, we run the function, and we hand the results back to the LLM. This keeps the system safe and controllable.

**Orchestration framework**: the library that runs the agent loop — manages memory, decides the order of tool calls, and passes data around. We use **LangGraph**.

### 2.8 ETL / data pipeline
**ETL** stands for **Extract, Transform, Load** — the classic pattern for moving data:
- **Extract**: fetch raw documents from government sources.
- **Transform**: clean the text, split it into chunks, create embeddings, extract entities/relationships.
- **Load**: write everything into Neo4j.
Our "data factory" is an ETL pipeline that runs on a schedule.

### 2.9 Chunking
LLMs and embeddings work best on smaller pieces of text, not entire 50-page PDFs. **Chunking** means splitting a document into smaller passages (e.g., 500–1,000 tokens each), usually with a small **overlap** between consecutive chunks so context isn't lost at the boundaries. We do this with a deterministic rule (not the LLM), which is faster, cheaper, and repeatable.

### 2.10 Entity and relationship extraction
An **entity** is a real-world thing mentioned in a document (an agency, a technology, a CVE, a policy, a standard). A **relationship** connects two entities with a verb (e.g., *requires*, *affects*, *supersedes*). We use the LLM to read each chunk and output a structured list of entities and relationships, which then become nodes and edges in the graph.

### 2.11 Idempotency
An operation is **idempotent** if running it many times produces the same result as running it once. Because our pipeline runs on a schedule, we must avoid creating duplicate copies of the same document each night. We achieve this with a **content hash** (a fingerprint of the text) and "upsert" logic (update if it exists, insert if it doesn't).

### 2.12 Evaluation (faithfulness / grounding)
Because we promise accurate, non-hallucinated answers, we **measure** answer quality with an evaluation tool (**Ragas**). It scores things like **faithfulness** (does the answer actually follow from the retrieved sources?) and **answer relevancy** (does it address the question?). This turns "trust us, it's accurate" into measurable evidence.

---

## 3. System architecture (high level)

The system has **two decoupled components**. "Decoupled" means they run independently and don't depend on each other being online at the same time — the pipeline fills the database in the background; the chatbot reads from it whenever a user asks.

```
                        ┌──────────────────────────────────────────┐
                        │        GOVERNMENT DATA SOURCES             │
                        │  CISA · NVD · Federal Register · CMS ·     │
                        │  ONC/HealthIT · NIST · DoD · NASCIO        │
                        └───────────────────┬────────────────────────┘
                                            │  (APIs / RSS / scrape)
                                            ▼
   COMPONENT 1: DATA FACTORY (scheduled ETL, runs in the background)
   ┌───────────────────────────────────────────────────────────────┐
   │ Extract → Clean → Chunk → Embed → LLM extract entities → Load   │
   └───────────────────────────────┬───────────────────────────────┘
                                    ▼
                       ┌────────────────────────────┐
                       │   NEO4J  (hybrid store)     │
                       │  Document/Chunk/Entity nodes│
                       │  + vector indexes           │
                       └─────────────┬───────────────┘
                                     ▲  (read-only tools)
                                     │
   COMPONENT 2: AGENTIC RAG CHATBOT (LangGraph agent)
   ┌───────────────────────────────────────────────────────────────┐
   │ User question → Agent reasons → calls tools (vector / graph /   │
   │ fulltext) → gathers grounded evidence → writes cited answer     │
   └───────────────────────────────┬───────────────────────────────┘
                                    ▼
                         Web console UI (FastAPI + custom frontend)
```

---

## 4. Component 1 — The Data Factory (ingestion pipeline), step by step

This is an automated ETL job scheduled to run on a fixed interval (e.g., nightly). Each run performs the following stages in order.

### Stage 1 — Extract (data ingestion via connectors)
We do **not** scrape blindly. We use a tiered connector strategy, preferring official structured endpoints. A **connector** is a small module that knows how to fetch one type of source and return a normalized list of raw documents.

- **Tier 1 — API connectors (most reliable):** call official JSON APIs. Example: the Federal Register API returns healthcare and defense rules as clean JSON with metadata (date, agency, document type).
- **Tier 2 — RSS/feed connectors:** subscribe to official update feeds (e.g., CISA alerts, CMS newsroom).
- **Tier 3 — Scrape connectors (last resort):** only for sources with no API/feed (e.g., some DoD memos, NASCIO PDF reports), using a Python fetcher plus an HTML/PDF text extractor.

All three implement the same interface, e.g. `fetch() -> list[RawDocument]`, so new sources can be added by writing one new connector — this is the "plug-and-play" scalability.

### Stage 2 — Clean
Raw web/PDF content contains navigation menus, headers, footers, ads, and markup. A cleaning step isolates the real body text and discards the noise, so downstream AI only sees meaningful content. (Tools: `trafilatura` / `BeautifulSoup` for HTML, `pypdf`/`pdfplumber` for PDFs.)

### Stage 3 — Deduplicate (idempotency check)
We compute a **content hash** of each cleaned document. If a document with the same hash already exists in Neo4j, we skip it (no reprocessing, no duplicate). If the content changed, we update it. This keeps scheduled runs cheap and the graph clean.

### Stage 4 — Chunk
Each new/changed document is split into overlapping chunks (deterministic splitter, e.g., ~800 tokens with ~100 token overlap). This is done **without** the LLM to save cost and guarantee repeatable results.

### Stage 5 — Embed
- Each **chunk** is converted into an embedding vector.
- Each **document** also gets a summary-level embedding for coarse matching.
Embeddings are generated by a **free local embedding model** (e.g., `BAAI/bge-small-en-v1.5` via `sentence-transformers`) — no API cost, no rate limits, and no data leaves our environment.

### Stage 6 — LLM extraction (summary + entities + relationships)
Each chunk (or document) is sent to the LLM with a strict prompt that returns structured output:
1. A short **summary**.
2. A list of **entities** (agencies, technologies, CVEs, policies, standards), each with a type/label.
3. A list of **relationships** between those entities (subject → verb → object).
We use a schema-constrained prompt (LangChain's `LLMGraphTransformer` pattern) so the output is reliable, parseable JSON.

### Stage 7 — Load into Neo4j (graph construction)
We write the results into the hybrid store using this graph model:

- **`Document` node** — one per source file. Stores metadata (source URL, agency, publish date, domain label, content hash) and the document-level embedding.
- **`Chunk` node** — one per chunk. Stores the chunk text and its embedding; linked to its parent `Document` via a `HAS_CHUNK` relationship.
- **`Entity` node** — one per unique real-world thing (deduplicated across documents). Linked to the chunks that mention it via `MENTIONS`.
- **Entity-to-entity relationships** — typed edges (e.g., `AFFECTS`, `ISSUED_BY`, `REQUIRES`) that encode connections within and, when entities overlap, across domains.

We create a **vector index** on chunk embeddings (for semantic search), a **full-text index** on text (for exact-term search), and **uniqueness constraints** on IDs (to keep loads idempotent).

### Stage 8 — Schedule
For a laptop demo, use `run_scheduled_ingest.ps1` (starts Neo4j if needed, waits for Bolt, runs ingest, stops Neo4j when safe) and register it with Windows Task Scheduler via `register_scheduled_ingest.ps1` (weekly or bi-weekly). Production would use an always-on host or managed Neo4j plus cron / cloud scheduler.

---

## 5. Component 2 — The Agentic RAG Chatbot, step by step

When an employee asks a question, the system uses an **agent** (an LLM driven by LangGraph) that reasons about how to answer and calls tools to fetch grounded evidence. The LLM never queries the database directly — it only calls the tools below.

### The agent's tools (each is a Python function)

In practice, **most day-to-day questions** are answered with `vector_search` and/or `fulltext_search`. `graph_search` is a **supporting** tool for relationship-style questions.

1. **`vector_search(query, domain?)` — semantic search (primary for conceptual Qs).**
   Embeds the user's question, then finds chunk nodes with the most similar embeddings (cosine) in Neo4j. Optional domain filter. Returns passage text + source metadata (title, agency, URL).

2. **`fulltext_search(term)` — keyword / identifier search (primary for CVE ids, dockets, exact phrases).**
   Uses Neo4j's full-text index (plus document id/title matching) where embeddings are weak. Returns passage text + citations.

3. **`graph_search(entity_name)` — entity & relationship lookup (secondary).**
   Finds matching `Entity` nodes, their `RELATED_TO` neighbors, and provenance chunks (`MENTIONS` → `Document` URL). Best for questions about how things are connected (e.g., CISA ↔ BOD, CVE ↔ product). Cross-domain hops work when the same entities appear across sources.

> Optional future: sandboxed `text2cypher` for open-ended Cypher — not required for the MVP.

### The reasoning loop (how the agent works)
1. Receive the user's question.
2. The LLM decides which tool(s) to call and with what arguments.
3. LangGraph executes the tool(s) and returns evidence to the LLM.
4. If evidence is insufficient, the agent may call another tool.
5. Once satisfied, it composes the final answer **with mandatory citations** (source title + URL for factual claims).
6. **Guardrail:** if tools return no useful evidence (or scores fall below threshold), the agent replies *"This is not covered in the current sources"* instead of guessing.

### The user interface
A clean internal web console built with **FastAPI** and a custom HTML/CSS/JS frontend (Intelligence Hub, Ask Assistant, Corpus, Data Factory, About). Employees type questions in plain English and see answers with clickable source links.

---

## 6. Data sources and how each is accessed

| Domain | Source | Access method (all free) |
|---|---|---|
| Cybersecurity & Defense | CISA Known Exploited Vulnerabilities (KEV) | Public JSON feed (no key) |
| Cybersecurity & Defense | CVE / vulnerability details | NVD API 2.0 (free API key for higher limits) |
| Cybersecurity & Defense | CISA alerts / advisories | Official RSS feeds |
| Cybersecurity & Defense | DoD CIO memos & AI directives | Fetch + PDF/HTML parse (no API) |
| Health IT & Civilian | CMS + ONC/HHS rules & notices | Federal Register API (free, no key) |
| Health IT & Civilian | CMS Newsroom / HealthIT.gov | RSS feeds |
| Health IT & Civilian | Public comments / dockets | Regulations.gov API (free key; DEMO_KEY for testing) |
| Tech Standards & Safety | NIST RMF / 800-53 / CSF | NIST CSRC data; 800-53 as structured OSCAL JSON |
| State & Local Gov | NASCIO priority reports | Fetch + PDF parse (no API) |

**Note:** all sources are public U.S. government information, so there are no confidentiality/CUI (Controlled Unclassified Information) concerns for this project.

---

## 7. Full technology stack with deep descriptions

| Layer | Technology | What it is and why we use it |
|---|---|---|
| **Language** | Python 3.11+ | Standard language for AI/ML; all libraries below are Python-native. |
| **Orchestration** | **LangGraph** | A framework (from the LangChain team) for building agents as a graph of steps. It manages the agent's memory, state, the tool-calling loop, and conditional logic (e.g., "search again if not enough evidence"). We use it to run Component 2's reasoning loop. |
| **LLM interface** | **LangChain** | A library that gives a single, uniform way to call many different LLMs and embedding models. Because of it, swapping Gemini for Groq or a local model is a one-line change. |
| **Primary LLM** | **Groq free tier (`llama-3.3-70b-versatile`)** | Strong tool-calling and higher free throughput for bulk extraction + agent loops. |
| **Fallback LLM** | **Google Gemini free tier** | Used if Groq fails; note free tiers have request/day limits that can be tight for bulk jobs. |
| **Optional local LLM** | **Ollama** | Runs open models (Llama/Qwen) entirely on our own machine — unlimited, offline, free. Useful for heavy bulk ingestion. |
| **Embeddings** | **`sentence-transformers` (`bge-small-en-v1.5`)** | Converts text to vectors locally, for free, with no rate limits and full data privacy. |
| **Hybrid database** | **Neo4j** (Community via Docker, or AuraDB Free) | Stores the knowledge graph *and* the vectors in one place. Community/Docker avoids the free-cloud idle-pause and node cap. Query language: **Cypher**. |
| **Ingestion — HTML** | **trafilatura / BeautifulSoup** | Extract clean body text from web pages, discarding menus/ads/boilerplate. |
| **Ingestion — PDF** | **pypdf / pdfplumber** | Extract text from PDF documents (DoD memos, NASCIO reports). |
| **HTTP / API calls** | **httpx / requests** | Fetch data from the government APIs and feeds. |
| **Feeds** | **feedparser** | Parse RSS/Atom update feeds. |
| **Chunking** | **LangChain text splitters** | Deterministically split documents into overlapping chunks. |
| **Extraction schema** | **LangChain `LLMGraphTransformer` + Pydantic** | Force the LLM to return structured, validated entities/relationships as JSON. |
| **User interface** | **FastAPI** + custom frontend | Hosted console with Hub, Ask Assistant, Corpus, Data Factory, and About workspaces. |
| **Evaluation** | **Ragas** | Measures answer faithfulness and relevancy against retrieved sources — our proof of "grounded, low-hallucination" answers. |
| **Scheduling** | **cron / GitHub Actions / Task Scheduler** | Triggers the ingestion pipeline automatically on a schedule. |
| **Config / secrets** | **python-dotenv** | Keeps API keys out of the code, in a local `.env` file. |

---

## 8. End-to-end examples (to make it concrete)

**Ingestion (scheduled / on-demand batch):**
1. Connectors fetch recent CISA KEV entries, NVD CVEs, Federal Register notices (CMS/HHS/DoD), NIST materials, etc.
2. Text is cleaned, chunked, and embedded locally (`bge-small-en-v1.5`).
3. The LLM extracts entities/relationships (e.g., `CVE-… —[AFFECTS]→ Product`, `CISA —[ISSUED_BY]→ BOD 26-04`).
4. Neo4j upserts Documents, Chunks (with embeddings), Entities, and links — incrementally (unchanged hashes are skipped).

**Typical employee questions (primary path — vector / fulltext):**
> "What is CVE-2026-56291, what product does it affect, and what's the remediation deadline?"

1. Agent calls `fulltext_search` on the CVE id.
2. Answers from the retrieved passage(s) with the source URL (e.g., NVD/CISA).

> "According to NIST, how should we structure cybersecurity risk management?"

1. Agent calls `vector_search`.
2. Cites CSF / RMF / 800-53 passages from Neo4j.

**Relationship-style question (secondary path — may use graph):**
> "Are there CISA-required actions or BOD guidance tied to the latest KEV entries?"

1. Agent may combine vector/fulltext with `graph_search("CISA")` / related entities.
2. Returns BOD / remediation guidance with citations — or states what is *not* evidenced if links are missing.

**Cross-domain question (capability when the graph is dense enough):**
> "Do any newly known-exploited vulnerabilities put systems behind our CMS EHR obligations at risk?"

Same agent loop; success depends on shared entities existing across cyber and health sources. If no grounded path exists, the agent refuses to invent one.

---

## 9. Cost model — fully free

| Item | Cost | How |
|---|---|---|
| LLM (reasoning + extraction) | **$0** | Groq free tier (+ Gemini free fallback / optional local Ollama) |
| Embeddings | **$0** | Local `sentence-transformers` model |
| Database | **$0** | Neo4j Community (Docker) or AuraDB Free |
| Government data | **$0** | All public APIs / feeds |
| Hosting (demo) | **$0–low** | Local machine, or Azure for Students $100 credit for a VM if cloud hosting is desired |

**Note on paid services:** We deliberately avoid a hard dependency on the paid OpenAI API. Student credits (e.g., Azure for Students) do **not** reliably grant OpenAI model access. Groq + Gemini free tiers (plus local embeddings) are sufficient for the MVP. The `.edu` GitHub Student Pack is still worth claiming for extras (Copilot, cloud credits).

---

## 10. Implementation roadmap (phased)

**Phase 1 — Environment & connectors.**
Set up Python environment and Neo4j (local Community / portable install or Docker). Build Tier-1/Tier-2 connectors for CISA KEV, NVD, Federal Register (CMS/HHS/DoD), NIST, Regulations.gov, and documented fallbacks where sites block scrapers. Deliverable: raw documents fetching reliably.

**Phase 2 — Ingestion pipeline & graph population.**
Implement clean → dedup → chunk → embed → LLM-extract → load. Define Neo4j schema, constraints, vector index, and full-text index. Deliverable: populated hybrid store + incremental `run_pipeline.py`.

**Phase 3 — Agentic tooling & interface.**
Implement `vector_search`, `fulltext_search`, and `graph_search`. Wire LangGraph with citation enforcement and no-evidence guardrail. Build FastAPI web console. Deliverable: working chatbot + practical employee Q evaluation set.

**Phase 4 — Validation & review.**
Run a fixed set of **practical employee questions** (plus refuse checks). Optionally run Ragas for faithfulness. Present demo to leadership: cited Q&A first; graph relations as supporting capability. Deliverable: measured/demo quality + outline updated to the refined problem statement.

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Free LLM rate limits during large ingestion | Throttle + retries; Groq primary; Gemini fallback; batch/limit docs per run; optional Ollama for bulk |
| Neo4j AuraDB Free pauses / caps | Prefer local Neo4j Community (portable zip or Docker) for development |
| LLM hallucination | Mandatory citations; similarity / no-evidence refuse; optional Ragas |
| Overselling graph for every query | Pitch cited RAG as primary; graph as secondary; densify overlapping entities for demos |
| Source website/API blocks (403) | Prefer APIs/feeds; isolate failures per connector; seed/fallback docs where needed |
| Entity duplication in graph | Idempotent upserts + content hashing + uniqueness constraints |
| Government data quality/inconsistency | Cleaning stage + schema-validated extraction (Pydantic) |

---

## 12. Success criteria

- The pipeline ingests target sources (on demand or on a schedule) with **no duplicate** documents for unchanged content.
- Employees can ask **practical** cyber/regulatory/NIST/health-IT questions and get answers **grounded in retrieved passages** with **source URLs**.
- The agent reliably chooses tools (vector vs fulltext vs graph) appropriate to the question type.
- Relationship/`graph_search` questions work when entities/edges exist (including cross-domain **when** the graph connects them); otherwise the system refuses to invent links.
- Off-corpus questions are refused (*"not covered in the current sources"*).
- Adding a new data source requires mainly writing/configuring one new connector (modular scalability).
- Optional: Ragas faithfulness meets an agreed threshold on a fixed test set.

---

## 13. Glossary quick reference

- **LLM** — AI that reads/writes text (the "brain").
- **Embedding** — numeric vector representing meaning of text.
- **Vector/semantic search** — finding text by meaning, not keywords.
- **Knowledge graph** — data stored as nodes + relationships.
- **Neo4j / Cypher** — the graph database / its query language.
- **RAG** — retrieve real documents, then let the LLM answer from them.
- **Agent / Agentic RAG** — an LLM that decides its own retrieval steps using tools.
- **Tool (function calling)** — a Python function the LLM can call to fetch data.
- **LangGraph / LangChain** — frameworks that run the agent and connect the models.
- **ETL** — Extract, Transform, Load (the data pipeline pattern).
- **Chunking** — splitting documents into smaller passages.
- **Entity/relationship extraction** — pulling structured facts out of text.
- **Idempotency** — safe to run repeatedly without duplicating data.
- **Hallucination** — AI making things up; prevented here via grounding + citations.
- **Ragas** — tool that scores answer faithfulness/relevancy.

---

*End of outline.*
