# SignalPulse AI

**Automated public-sector intelligence from official U.S. government sources — collected, indexed, and queried with citations.**

SignalPulse AI monitors selected federal and state public sources, stores them in a searchable knowledge graph, and answers questions in plain English with links back to the original documents. It is built for teams that need to stay current on cyber, health IT, standards, and regulatory updates without manually checking dozens of `.gov` sites.

For the full design narrative (problem statement, concepts, architecture), see [`SignalPulse_AI_Project_Outline.md`](./SignalPulse_AI_Project_Outline.md).

---

## What it does

| Capability | Description |
|---|---|
| **Scheduled collection** | Pulls from official APIs, RSS/Atom feeds, and HTML/PDF sources across cybersecurity, healthcare, technology standards, federal regulation, and state CIO priorities. |
| **Incremental ingestion** | Content-hash deduplication so only new or changed documents are reprocessed — suitable for weekly or bi-weekly refresh runs. |
| **Knowledge graph** | Documents, passages, embeddings, and LLM-extracted entities/relationships stored in **Neo4j** with vector and fulltext indexes. |
| **Agentic RAG chat** | An LLM agent chooses among vector, fulltext, and graph retrieval before composing an answer — always grounded in ingested sources or explicitly refusing when coverage is missing. |
| **Ingest digest & watchlist** | After each run, a digest summarizes new/updated documents and flags items matching configurable watchlist keywords (e.g. ransomware, NIST CSF, prior authorization). |
| **Web console** | A FastAPI-backed dashboard for corpus overview, document browse, pipeline status, digest/alerts, and cited Q&A. |

### Sources in scope

CISA Known Exploited Vulnerabilities · NVD CVEs · Federal Register (CMS, HHS/ONC, Defense) · NIST news, CSF, RMF, SP 800-53 · HealthIT.gov · NASCIO state CIO priorities · agency reference seeds

All ingested material is **public U.S. government information** — no confidential or proprietary data is collected or stored.

---

## How it works

```
  Official sources          Ingestion pipeline              Neo4j store              Agentic RAG
  (APIs · RSS · HTML/PDF)   Extract → Clean → Chunk         Documents                Vector search
                            → Embed → Extract entities  →   Passages + embeddings →  Fulltext search
                            → Load graph                    Entities + relationships Graph search
                                                                                      ↓
                                                                                 Cited answer
                                                                                 (or clear refuse)
```

1. **Connectors** fetch and normalize content into a common document format regardless of source type (JSON, XML, HTML, PDF).
2. **Processing** cleans text, splits it into overlapping passages, and generates local embeddings (`BAAI/bge-small-en-v1.5` — no embedding API required).
3. **Extraction** uses an LLM to pull entities and typed relationships from each passage.
4. **Loading** writes documents, chunks, vectors, and graph links into Neo4j.
5. **Retrieval** exposes three tools — semantic vector search, keyword fulltext search, and graph traversal — that an agent selects based on the question.
6. **Digest** records what changed in each run and surfaces watchlist matches in the console and `data/processed/digest_latest.md`.

Embeddings run locally; LLM calls use free-tier API providers (Groq primary, with optional fallbacks).

---

## Web console

The console runs at `http://localhost:8501` and provides five workspaces:

| View | Purpose |
|---|---|
| **Intelligence Hub** | Corpus KPIs, charts, recent documents, last ingest summary |
| **Ask Assistant** | Plain-English Q&A with clickable source citations |
| **Corpus** | Browse and search every ingested document |
| **Data Factory** | Pipeline stages, connector catalog, ingest digest & watchlist alerts |
| **About** | Project overview and grounding policy |

Launch with Neo4j running:

```powershell
.\start_neo4j.ps1
.\run_chat.ps1
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 / 3.12 |
| Graph database | Neo4j Community 5.26 (local, portable runtime) |
| Embeddings | `sentence-transformers` / BGE-small-en-v1.5 (local) |
| LLM | Groq (primary), Mistral / DeepSeek / Gemini (fallbacks) |
| Agent & tools | LangChain-style agentic loop with vector, fulltext, graph tools |
| Web UI | FastAPI + custom HTML/CSS/JS |
| Scheduling | Windows Task Scheduler (optional unattended ingest) |

---

## Getting started

### Prerequisites

- Python 3.11 or 3.12
- Git
- At least one free LLM API key ([Groq](https://console.groq.com/keys) recommended)
- Windows PowerShell (primary scripts; core Python package is cross-platform)

Neo4j is provided via a portable local runtime — no Docker or cloud account required.

### Installation

```powershell
git clone https://github.com/Saihaj-coder/SignalPulse_AI.git
cd SignalPulse_AI

py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt

Copy-Item .env.example .env
# Edit .env — set NEO4J_PASSWORD and at least GROQ_API_KEY
```

Verify the environment:

```powershell
python -m signalpulse.check_setup
```

Start Neo4j (first run downloads the portable JDK + Neo4j into `runtime/`, gitignored):

```powershell
.\start_neo4j.ps1
# Browser: http://localhost:7474  ·  Bolt: bolt://localhost:7687
```

### First ingest

```powershell
python run_pipeline.py --profile demo
# or: .\run_demo_ingest.ps1
```

Profiles: `demo` / `weekly` (richer cyber + NIST + CMS pull) · `full` (all sources) · `smoke` (plumbing check). List all with `python run_pipeline.py --list-profiles`.

Each successful run writes `data/processed/last_ingest.json` and an ingest digest. Re-runs are incremental — unchanged documents are skipped.

### API keys

| Variable | Purpose | Required |
|---|---|---|
| `GROQ_API_KEY` | Primary LLM | Recommended |
| `MISTRAL_API_KEY` | Fallback LLM | Optional |
| `DEEPSEEK_API_KEY` | Fallback LLM | Optional |
| `GOOGLE_API_KEY` | Gemini fallback | Optional |
| `NVD_API_KEY` | Higher NVD rate limits | Optional |
| `REGULATIONS_GOV_API_KEY` | Regulations.gov | Optional |

Embeddings require no key. Neo4j uses the password set in `.env` (`NEO4J_PASSWORD`).

> **Security:** Copy `.env.example` to `.env` locally — never commit `.env`. The `runtime/` and `data/raw/` + `data/processed/` directories are gitignored and stay on the machine where ingestion runs.

---

## Automation

Unattended ingest is supported on Windows via Task Scheduler. The runner starts Neo4j if needed, waits for connectivity, runs the pipeline, writes the digest, logs to `data/processed/scheduled_ingest.log`, and stops Neo4j when safe (unless the chat console is active).

```powershell
# Test the unattended flow once
.\run_scheduled_ingest.ps1 -IngestProfile weekly

# Register a weekly job (default: Sunday 13:00, 3-month trial window)
.\register_scheduled_ingest.ps1 -Cadence Weekly -Time 13:00

# Remove the scheduled task
.\register_scheduled_ingest.ps1 -Unregister
```

Bi-weekly mode skips runs when the last successful ingest was fewer than 13 days ago.

---

## Watchlist & ingest digest

Watchlist keywords live in `data/seeds/watchlist.txt` (one term per line; `#` comments allowed). After each pipeline run:

- **`data/processed/digest_latest.md`** — human-readable summary of new/updated documents
- **`data/processed/digest_latest.json`** — machine-readable digest for the console
- **Data Factory view** — metric cards, watchlist chips, alert highlights, and per-domain change lists

This provides lightweight monitoring: the system surfaces what landed and which items match configured topics, without requiring a manual query each week.

---

## Project structure

```
SignalPulse_AI/
├── signalpulse/               # Core package (connectors, pipeline, retrieval, agent)
├── web/                       # Console frontend (HTML/CSS/JS)
├── notebooks/                 # Step-by-step build notebooks
├── data/seeds/                # Watchlist, NASCIO text, agency glossary
├── webapp.py                  # FastAPI backend
├── run_pipeline.py            # Ingestion CLI
├── run_scheduled_ingest.ps1   # Unattended ingest runner
├── register_scheduled_ingest.ps1
├── start_neo4j.ps1 / stop_neo4j.ps1
├── run_chat.ps1               # Launch web console
├── run_eval.py                # Practical Q&A evaluation
├── requirements.txt
├── .env.example
└── SignalPulse_AI_Project_Outline.md
```

Notebooks document the build story; the `signalpulse/` package is what the pipeline, console, and evaluation scripts import at runtime.

---

## Build status

| Step | Deliverable | Status |
|---|---|---|
| 0 | Project setup | Done |
| 1 | Neo4j schema | Done |
| 2 | Data connectors | Done |
| 3 | Clean, chunk & embed | Done |
| 4 | LLM entity extraction | Done |
| 5 | Graph loader | Done |
| 6 | Ingestion pipeline | Done |
| 7 | Retrieval tools | Done |
| 8 | Agentic RAG | Done |
| 9 | Web console | Done |
| 10 | Evaluation | Done |
| 11 | Scheduled ingest & digest alerts | Done |

---

## Roadmap

- **Hosted deployment** — FastAPI console + managed Neo4j, HTTPS, SSO for internal teams
- **Push notifications** — email or Teams/Slack delivery for digest and watchlist alerts
- **Expanded evaluation** — broader question sets, refuse-rate tracking, optional Ragas CI checks
- **Additional connectors** — more official APIs/feeds with documented fallbacks
- **Operational polish** — ingest history UI, source failure alerting, batch tuning

---

## Disclaimer

SignalPulse AI is a demonstration and evaluation prototype. It is not legal or compliance advice. Collection runs on a schedule or on demand — not as a live second-by-second feed. For decisions that matter, always confirm details on the original `.gov` source page.

---

*Public U.S. government information only. Built with Neo4j, LangChain-style agentic retrieval, and local embeddings.*
