# SignalPulse AI

**Public-sector intelligence from official U.S. government sources — collected, indexed, and asked with citations.**

Prepared for 22nd Century Technologies, Inc.

**SignalPulse AI** is an internal tool for teams that support federal and state clients. It does two jobs that are usually manual today:

1. **Automatically collect and refresh** selected **public U.S. government** sources across **cybersecurity**, **health IT**, **technology standards & risk frameworks**, **federal regulation**, and **state CIO priorities** (for example CISA KEV/alerts, NVD CVEs, Federal Register / CMS / ONC–HealthIT notices, NIST publications, and NASCIO materials).
2. **Answer employee questions in plain English** only from that live corpus — with **clickable source citations** — or clearly say the topic is **not covered** in current sources (no silent guessing from general LLM training data).

Under the hood, ingestion cleans, chunks, embeds, and extracts entities into **Neo4j** (documents + passages + embeddings + knowledge-graph links). An **agentic retrieval** layer then chooses among vector, fulltext, and graph tools before composing the answer. Day-to-day value is **fresh monitoring + trustworthy, cited Q&A**; cross-document / relationship lookups are a supporting strength when the graph connects entities.

For the full design (problem statement, concepts, architecture), see
[`SignalPulse_AI_Project_Outline.md`](./SignalPulse_AI_Project_Outline.md).

---

## Project structure

```
22nd Project/
├── notebooks/                 # Step-by-step build notebooks
├── signalpulse/               # Reusable Python package (ETL, retrieval, agent)
├── web/                       # Enterprise console (HTML/CSS/JS)
│   ├── index.html
│   └── static/
├── data/
│   ├── seeds/                 # Seed text (NASCIO, agency glossary) — tracked
│   ├── raw/                   # Local fetch cache — gitignored
│   └── processed/             # Ingest/eval stamps — gitignored
├── webapp.py                  # Primary UI backend (FastAPI)
├── run_chat.ps1               # Launch console (http://localhost:8501)
├── run_pipeline.py            # Ingestion pipeline CLI
├── run_demo_ingest.ps1        # Demo/weekly ingest helper
├── run_eval.py                # Practical question eval runner
├── start_neo4j.ps1 / stop_neo4j.ps1
├── requirements.txt
├── .env.example               # Copy to .env (never commit .env)
├── SignalPulse_AI_Project_Outline.md
└── README.md
```

The project uses a **hybrid** layout: notebooks tell the story and show outputs
for learning/demo; the `signalpulse/` package holds the reusable code that the
notebooks, chat UI, and `run_pipeline.py` all import.

> **Do not commit secrets.** Copy `.env.example` → `.env` locally. Neo4j under
> `runtime/` and corpus caches under `data/raw/` + `data/processed/` stay local.

---

## Prerequisites

1. **Python 3.11 or 3.12** (recommended for `torch` / `sentence-transformers` wheels).
2. **Neo4j** via this project's portable runtime (`.\start_neo4j.ps1`) — no Docker
   required. Docker Desktop / Neo4j Desktop also work if you prefer.
3. **Git** (for version control / GitHub).
4. At least one free LLM API key (Groq recommended) — see below.

---

## Setup (Windows / PowerShell)

Run these from the project root: `C:\Users\saihaj\Documents\22nd Project`

### 1. Create and activate a virtual environment (Python 3.12)

```powershell
# Create the venv using the 3.12 interpreter
py -3.12 -m venv .venv

# Activate it
.\.venv\Scripts\Activate.ps1
```

> If activation is blocked by execution policy, run once:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

You should now see `(.venv)` at the start of your prompt.

### 2. Install dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> This installs PyTorch (via `sentence-transformers`) and can take several
> minutes and a few GB the first time. CPU-only is fine — no GPU required.

### 3. Create your `.env` file

```powershell
Copy-Item .env.example .env
```

Then open `.env` and fill in your keys (see the next section).

### 4. Start Neo4j

This project uses a **portable, local Neo4j Community Server** (v5.26 LTS) with a
bundled Java runtime, installed under `runtime/` (git-ignored). No Docker and no
system-wide install required. Helper scripts start/stop it:

```powershell
.\start_neo4j.ps1     # start the database (takes ~20-30s)
.\stop_neo4j.ps1      # stop the database
```

- Browser UI: <http://localhost:7474> (login `neo4j` / `signalpulse123`)
- Bolt URI (used by the app): `bolt://localhost:7687`
- Startup logs: `runtime\neo4j-out.log`

> We run Neo4j **locally** rather than on AuraDB Free so it never pauses on
> idle and has no node-count cap. The APOC plugin and vector-index support are
> already configured.

> **First-time setup note:** the portable JDK + Neo4j are downloaded into
> `runtime/`. If you move the project to a new machine, re-run the setup so those
> are re-downloaded (they are intentionally excluded from git).

### 5. Verify the environment

```powershell
python -m signalpulse.check_setup
```

You should see `[ OK ]` for the packages and a config summary. A `[WARN]` on the
Neo4j connection is fine if you haven't started it yet.

---

## Free API keys — where to get each

All services below have **free tiers**; no paid account is required.

| Key | Used for | Where to get it | Required? |
|---|---|---|---|
| `GROQ_API_KEY` | Primary LLM (high free throughput + tools) | <https://console.groq.com/keys> | Recommended |
| `MISTRAL_API_KEY` | Fallback LLM when Groq is rate-limited | <https://console.mistral.ai/> | Optional |
| `DEEPSEEK_API_KEY` | Fallback LLM (key from platform.deepseek.com) | <https://platform.deepseek.com/api_keys> | Optional |
| `GOOGLE_API_KEY` | Gemini fallback (~20 req/day on free tier) | <https://aistudio.google.com/apikey> | Optional |
| `NVD_API_KEY` | Higher rate limit for CVE data | <https://nvd.nist.gov/developers/request-an-api-key> | Optional |
| `REGULATIONS_GOV_API_KEY` | Regulations.gov docket data | <https://open.gsa.gov/api/regulationsgov/> | Optional (`DEMO_KEY` works for light testing) |

Notes:
- Free-tier keys stay valid, but **daily/minute quotas** still apply. `ask()` tries
  providers in order (`LLM_PROVIDER`, then mistral → deepseek → gemini) on 429/quota.
- Get DeepSeek keys from **platform.deepseek.com** (format `sk-…`). Other `sk-lit-…`
  keys are not accepted by the official DeepSeek API.
- **Neo4j** needs no key — just the username/password you set when starting it.
- **Embeddings** run locally (`BAAI/bge-small-en-v1.5`) — no key, downloaded
  automatically on first use.

---

## Running the ingestion pipeline

Neo4j must be up first:

```powershell
.\start_neo4j.ps1
```

### Company demo / weekly refresh (recommended)

Pulls **overlapping cyber + NIST + CMS** sources at higher depth (~20 docs/source,
up to 4 chunks/doc) so chat answers and the knowledge graph are denser.
Seed/fallback sources (NASCIO text, HealthIT scrape) stay included when live
sites block automation.

```powershell
# One-shot richer ingest for demos
.\run_demo_ingest.ps1
# equivalent:
python run_pipeline.py --profile demo

# Weekly habit (same depth; incremental — skips unchanged docs)
.\run_demo_ingest.ps1 -Weekly
# equivalent:
python run_pipeline.py --profile weekly
```

Other profiles:

```powershell
python run_pipeline.py --list-profiles
python run_pipeline.py --profile full     # all sources, moderate depth
python run_pipeline.py --profile smoke    # tiny plumbing check
python run_pipeline.py --source cisa_kev --limit 5
```

Each successful run writes `data/processed/last_ingest.json` (timestamp + counts).
Incremental re-runs skip unchanged docs, so a second `demo`/`weekly` pass after
LLM quota resets will pick up any rate-limited failures (common on dense NIST
800-53 controls). Seed/fallback connectors remain when live .gov sites return 403.

### Chat UI (Step 9)

Custom enterprise console (FastAPI + HTML/CSS/JS) over the live Neo4j corpus and
`ask()` agent — Intelligence Hub with KPIs/charts/tables, Ask workspace with
citations, Corpus browser, and Data Factory view.

```powershell
.\start_neo4j.ps1
.\run_chat.ps1
# equivalent:
python -m uvicorn webapp:app --host 127.0.0.1 --port 8501 --reload
```

Then open http://localhost:8501.

---

## Build roadmap

| Step | Deliverable |
|---|---|
| 0 | Project setup (this step) |
| 1 | Neo4j connection + schema (`notebooks/01_neo4j_setup.ipynb`) |
| 2 | Data connectors (`notebooks/02_connectors.ipynb`) |
| 3 | Clean, chunk & embed (`notebooks/03_clean_chunk_embed.ipynb`) |
| 4 | LLM entity extraction (`notebooks/04_extract_entities.ipynb`) |
| 5 | Graph loader (`notebooks/05_load_graph.ipynb`) |
| 6 | Full ingestion pipeline (`run_pipeline.py`) |
| 7 | Retrieval tools (`notebooks/07_retrieval.ipynb`) |
| 8 | Agentic RAG loop (`notebooks/08_agent.ipynb`) |
| 9 | Chatbot UI (`webapp.py` + `web/` console) |
| 10 | Evaluation (`run_eval.py` / practical questions; Ragas optional) |
| 11 | Polish & demo |

---

*All data sources are public U.S. government information; no confidential data
is collected or stored.*
