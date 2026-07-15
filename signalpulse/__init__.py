"""SignalPulse AI — Agentic RAG for public-sector cyber & regulatory intelligence.

Reusable package for the ETL pipeline and the Agentic RAG chatbot. Notebooks
under ``notebooks/``, ``webapp.py``, and ``run_pipeline.py`` import from here.

Primary value: up-to-date, cited answers over ingested government sources.
Neo4j stores documents, chunks/embeddings, and a knowledge graph; the agent
uses vector, fulltext, and (when useful) graph retrieval tools.

Modules:
    config, graph, connectors, processing, extraction, loader, pipeline,
    retrieval, agent, eval_questions, llm
"""

__version__ = "0.1.0"
