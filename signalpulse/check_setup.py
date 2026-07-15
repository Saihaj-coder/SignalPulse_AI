"""Setup verification for SignalPulse AI.

Run this after installing dependencies and creating your ``.env`` file to
confirm the environment is ready before we start building features.

Usage (from the project root, with the virtual environment active):
    python -m signalpulse.check_setup
"""

from __future__ import annotations

import importlib
import sys

# Packages we expect to be installed, grouped for a readable report.
REQUIRED_IMPORTS: dict[str, str] = {
    "pydantic_settings": "config",
    "neo4j": "Neo4j driver",
    "httpx": "HTTP client",
    "feedparser": "RSS feeds",
    "bs4": "BeautifulSoup (HTML)",
    "trafilatura": "web text extraction",
    "pypdf": "PDF extraction",
    "pdfplumber": "PDF (tables)",
    "pandas": "CSV/tabular",
    "sentence_transformers": "local embeddings",
    "langchain": "LLM framework",
    "langgraph": "agent orchestration",
    "langchain_google_genai": "Gemini LLM",
    "langchain_groq": "Groq LLM",
    "langchain_mistralai": "Mistral LLM",
    "langchain_openai": "OpenAI-compatible (DeepSeek)",
    "fastapi": "web console API",
    "uvicorn": "ASGI server",
    "ragas": "evaluation",
}

CHECK = "[ OK ]"
FAIL = "[FAIL]"
WARN = "[WARN]"


def _mask(value: str | None) -> str:
    """Show only that a secret exists, never its full value."""
    if not value:
        return "(not set)"
    return f"set ({value[:4]}...{value[-2:]})" if len(value) > 6 else "set"


def check_imports() -> bool:
    """Try importing each required package; report any that are missing."""
    print("\n== Package imports ==")
    all_ok = True
    for module, label in REQUIRED_IMPORTS.items():
        try:
            importlib.import_module(module)
            print(f"  {CHECK} {module:<24} ({label})")
        except Exception as exc:  # noqa: BLE001 - we want to report any failure
            all_ok = False
            print(f"  {FAIL} {module:<24} ({label}) -> {exc}")
    return all_ok


def check_config() -> bool:
    """Load settings from .env and report which keys are configured."""
    print("\n== Configuration (.env) ==")
    try:
        from signalpulse.config import BASE_DIR, settings
    except Exception as exc:  # noqa: BLE001
        print(f"  {FAIL} Could not load settings: {exc}")
        return False

    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        print(f"  {WARN} No .env file found at {env_path}")
        print("         Copy .env.example to .env and fill in your keys.")

    print(f"  Neo4j URI            : {settings.NEO4J_URI}")
    print(f"  Neo4j user           : {settings.NEO4J_USERNAME}")
    print(f"  Neo4j password       : {_mask(settings.NEO4J_PASSWORD)}")
    print(f"  Google/Gemini key    : {_mask(settings.GOOGLE_API_KEY)}")
    print(f"  Groq key             : {_mask(settings.GROQ_API_KEY)}")
    print(f"  Mistral key          : {_mask(settings.MISTRAL_API_KEY)}")
    print(f"  DeepSeek key         : {_mask(settings.DEEPSEEK_API_KEY)}")
    print(f"  LLM primary          : {settings.LLM_PROVIDER}")
    print(f"  NVD key (optional)   : {_mask(settings.NVD_API_KEY)}")
    print(f"  Regulations.gov key  : {_mask(settings.REGULATIONS_GOV_API_KEY)}")
    print(f"  Embedding model      : {settings.EMBEDDING_MODEL} (dim={settings.EMBEDDING_DIM})")

    from signalpulse.llm import available_providers

    providers = available_providers()
    print(f"  Provider chain       : {' -> '.join(providers) if providers else '(none)'}")

    if not providers:
        print(
            f"  {WARN} No LLM key set yet "
            "(add GROQ_API_KEY / MISTRAL_API_KEY / DEEPSEEK_API_KEY / GOOGLE_API_KEY)."
        )
    return True


def check_neo4j() -> bool:
    """Attempt a live connection to Neo4j (optional; only if driver present)."""
    print("\n== Neo4j connection ==")
    try:
        from neo4j import GraphDatabase

        from signalpulse.config import settings
    except Exception as exc:  # noqa: BLE001
        print(f"  {WARN} Skipped (driver/config unavailable): {exc}")
        return False

    try:
        driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
        )
        driver.verify_connectivity()
        driver.close()
        print(f"  {CHECK} Connected to {settings.NEO4J_URI}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  {WARN} Could not connect to Neo4j: {exc}")
        print("         Make sure Neo4j is running (see README) — this is fine")
        print("         to skip until Step 1.")
        return False


def main() -> int:
    print("=" * 64)
    print(" SignalPulse AI — environment check")
    print("=" * 64)
    print(f" Python: {sys.version.split()[0]}")

    imports_ok = check_imports()
    config_ok = check_config()
    check_neo4j()  # informational only; not required to pass Step 0

    print("\n" + "=" * 64)
    if imports_ok and config_ok:
        print(" Result: environment looks good. Ready for Step 1.")
        return 0
    print(" Result: some checks failed — see [FAIL] lines above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
