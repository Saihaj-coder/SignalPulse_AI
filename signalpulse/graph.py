"""Neo4j connection and schema management for SignalPulse AI.

This module is the single place that knows how to talk to our Neo4j database.
It provides:

* a shared database driver (``get_driver`` / ``close_driver``),
* a simple query helper (``run_query``),
* the project's graph **schema** — the node types, uniqueness constraints,
  vector indexes, and full-text indexes — plus functions to create, inspect,
  and reset them.

Graph model (see SignalPulse_AI_Project_Outline.md, section 4)
--------------------------------------------------------------
Nodes:
    (:Document)  one per source document. Holds metadata + a document-level
                 summary embedding.
    (:Chunk)     a passage of a document. Holds the chunk text + its embedding.
    (:Entity)    a real-world thing (agency, technology, CVE, policy, ...).

Relationships:
    (:Document)-[:HAS_CHUNK]->(:Chunk)     a document is split into chunks
    (:Chunk)-[:MENTIONS]->(:Entity)        a chunk mentions an entity
    (:Entity)-[:RELATED_TO {type}]->(:Entity)   typed link between entities
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver, GraphDatabase

from signalpulse.config import settings

# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

_driver: Driver | None = None


def get_driver() -> Driver:
    """Return a shared Neo4j driver, creating it on first use.

    A driver is a thread-safe connection pool. We keep one instance for the
    whole process instead of reconnecting on every query.
    """
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
        )
    return _driver


def close_driver() -> None:
    """Close the shared driver (call when completely done, e.g. app shutdown)."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def verify_connectivity() -> bool:
    """Raise if the database is unreachable; return True if reachable."""
    get_driver().verify_connectivity()
    return True


def run_query(
    query: str,
    parameters: dict[str, Any] | None = None,
    database: str | None = None,
) -> list[dict[str, Any]]:
    """Run a Cypher query and return the rows as a list of dictionaries."""
    driver = get_driver()
    db = database or settings.NEO4J_DATABASE
    with driver.session(database=db) as session:
        result = session.run(query, parameters or {})
        return [record.data() for record in result]


# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

# Uniqueness constraints. Each also creates a backing index, which makes
# MERGE/MATCH on these keys fast and guarantees we never create duplicates.
CONSTRAINTS: list[tuple[str, str]] = [
    (
        "document_id",
        "CREATE CONSTRAINT document_id IF NOT EXISTS "
        "FOR (d:Document) REQUIRE d.id IS UNIQUE",
    ),
    (
        "chunk_id",
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS "
        "FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
    ),
    (
        "entity_name",
        "CREATE CONSTRAINT entity_name IF NOT EXISTS "
        "FOR (e:Entity) REQUIRE e.name IS UNIQUE",
    ),
]

# Vector indexes power semantic (meaning-based) search over embeddings.
# Each tuple: (index_name, node_label, embedding_property).
VECTOR_INDEXES: list[tuple[str, str, str]] = [
    ("chunk_embedding", "Chunk", "embedding"),
    ("document_embedding", "Document", "embedding"),
]

# Full-text indexes power exact keyword search (e.g. a specific CVE id).
# Each tuple: (index_name, cypher_statement).
FULLTEXT_INDEXES: list[tuple[str, str]] = [
    (
        "chunk_fulltext",
        "CREATE FULLTEXT INDEX chunk_fulltext IF NOT EXISTS "
        "FOR (c:Chunk) ON EACH [c.text]",
    ),
    (
        "document_fulltext",
        "CREATE FULLTEXT INDEX document_fulltext IF NOT EXISTS "
        "FOR (d:Document) ON EACH [d.title, d.summary]",
    ),
]


def create_constraints() -> list[str]:
    """Create all uniqueness constraints. Returns the names created."""
    for _, statement in CONSTRAINTS:
        run_query(statement)
    return [name for name, _ in CONSTRAINTS]


def create_vector_indexes(dimensions: int | None = None) -> list[str]:
    """Create vector indexes sized to our embedding model's dimension.

    ``dimensions`` defaults to ``settings.EMBEDDING_DIM`` (384 for bge-small).
    Similarity function is cosine, which pairs with normalized embeddings.
    """
    dim = dimensions or settings.EMBEDDING_DIM
    for name, label, prop in VECTOR_INDEXES:
        statement = (
            f"CREATE VECTOR INDEX {name} IF NOT EXISTS "
            f"FOR (n:{label}) ON (n.{prop}) "
            f"OPTIONS {{ indexConfig: {{ "
            f"`vector.dimensions`: {dim}, "
            f"`vector.similarity_function`: 'cosine' "
            f"}} }}"
        )
        run_query(statement)
    return [name for name, _, _ in VECTOR_INDEXES]


def create_fulltext_indexes() -> list[str]:
    """Create all full-text indexes. Returns the names created."""
    for _, statement in FULLTEXT_INDEXES:
        run_query(statement)
    return [name for name, _ in FULLTEXT_INDEXES]


def create_schema(dimensions: int | None = None) -> None:
    """Create the full schema: constraints + vector + full-text indexes.

    Safe to run repeatedly — every statement uses ``IF NOT EXISTS``.
    """
    create_constraints()
    create_vector_indexes(dimensions)
    create_fulltext_indexes()


def drop_schema() -> None:
    """Remove all SignalPulse constraints and indexes (for a clean reset).

    Does NOT delete data — see ``clear_data`` for that.
    """
    for name, _ in FULLTEXT_INDEXES:
        run_query(f"DROP INDEX {name} IF EXISTS")
    for name, _, _ in VECTOR_INDEXES:
        run_query(f"DROP INDEX {name} IF EXISTS")
    for name, _ in CONSTRAINTS:
        run_query(f"DROP CONSTRAINT {name} IF EXISTS")


# ---------------------------------------------------------------------------
# Inspection helpers
# ---------------------------------------------------------------------------

def show_constraints() -> list[dict[str, Any]]:
    """Return all constraints currently defined in the database."""
    return run_query("SHOW CONSTRAINTS")


def show_indexes() -> list[dict[str, Any]]:
    """Return all indexes currently defined in the database."""
    return run_query("SHOW INDEXES")


def node_counts() -> dict[str, int]:
    """Return a count of nodes per label (Document / Chunk / Entity)."""
    counts: dict[str, int] = {}
    for label in ("Document", "Chunk", "Entity"):
        rows = run_query(f"MATCH (n:{label}) RETURN count(n) AS c")
        counts[label] = rows[0]["c"] if rows else 0
    return counts


def clear_data() -> None:
    """Delete ALL nodes and relationships (keeps the schema). Use with care."""
    run_query("MATCH (n) DETACH DELETE n")
