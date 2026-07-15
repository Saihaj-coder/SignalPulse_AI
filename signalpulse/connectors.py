"""Data connectors for SignalPulse AI (the "Extract" stage).

Every source of government data is wrapped in a **connector**. No matter what
format a source uses (JSON API, RSS/XML feed, HTML page, or PDF), a connector's
``fetch()`` method returns a list of the same normalized object: ``RawDocument``.
The rest of the pipeline (clean -> chunk -> embed -> load) only ever sees
``RawDocument``, so it doesn't care where the data came from.

Three tiers, preferred in this order (most reliable first):

    Tier 1  API      -> official JSON APIs      (CISA KEV, NVD, Federal Register)
    Tier 2  RSS      -> official Atom/RSS feeds (CISA advisories, CMS, ...)
    Tier 3  scrape   -> HTML / PDF extraction   (DoD memos, NASCIO reports, ...)
"""

from __future__ import annotations

import hashlib
import io
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser
import httpx
import pdfplumber
import trafilatura
from bs4 import BeautifulSoup
from pypdf import PdfReader
from tenacity import retry, stop_after_attempt, wait_exponential

from signalpulse.config import BASE_DIR, settings

# Browser-like UA reduces (but does not eliminate) Akamai/Cloudflare blocks on
# some .gov sites. Prefer official APIs when a feed returns 403.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 SignalPulse-AI/0.1"
)

# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------

_HTTP: httpx.Client | None = None


def _client() -> httpx.Client:
    """Return a shared HTTP client (connection reuse, sane defaults)."""
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.Client(
            timeout=45.0,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, application/xml, text/html, */*",
            },
            follow_redirects=True,
        )
    return _HTTP


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _get(url: str, **kwargs: Any) -> httpx.Response:
    """HTTP GET with automatic retry/backoff for transient failures."""
    resp = _client().get(url, **kwargs)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# The normalized document object
# ---------------------------------------------------------------------------


@dataclass
class RawDocument:
    """One document, normalized to a common shape regardless of source format."""

    source_id: str          # stable id from the source (e.g. CVE id, doc number)
    title: str
    url: str                # link back to the original (used later for citations)
    agency: str             # e.g. "CISA", "CMS"
    domain: str             # e.g. "Cybersecurity & Defense"
    raw_text: str           # cleaned body text — always plain text at this point
    source_format: str      # "json" | "xml" | "pdf" | "html" | "csv"
    connector: str          # which connector produced this
    published_date: str | None = None
    content_hash: str = field(default="")

    def __post_init__(self) -> None:
        # Fingerprint the text so the pipeline can skip unchanged documents.
        if not self.content_hash:
            basis = f"{self.source_id}\n{self.raw_text}".encode("utf-8")
            self.content_hash = hashlib.sha256(basis).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def preview(self, n: int = 240) -> str:
        text = " ".join(self.raw_text.split())
        snippet = text[:n] + ("..." if len(text) > n else "")
        return (
            f"[{self.source_format.upper()}] {self.agency} | {self.domain}\n"
            f"  id    : {self.source_id}\n"
            f"  title : {self.title}\n"
            f"  date  : {self.published_date}\n"
            f"  url   : {self.url}\n"
            f"  text  : {snippet}"
        )


# ---------------------------------------------------------------------------
# Base connector
# ---------------------------------------------------------------------------


class Connector(ABC):
    """Interface every connector implements. One method: ``fetch``."""

    name: str = "base"
    agency: str = ""
    domain: str = ""
    tier: str = ""  # "api" | "rss" | "scrape"

    @abstractmethod
    def fetch(self, limit: int = 10) -> list[RawDocument]:
        """Return up to ``limit`` normalized documents from this source."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _html_to_text(html: str) -> str:
    """Strip HTML tags to plain text (used for RSS summaries)."""
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text(" ", strip=True)


def _extract_pdf_text(data: bytes, max_pages: int = 15) -> str:
    """Extract text from PDF bytes. Try fast pypdf, fall back to pdfplumber."""
    text = ""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:max_pages]
        text = "\n".join((p.extract_text() or "") for p in pages)
    except Exception:
        text = ""
    if text.strip():
        return text
    # Fallback: pdfplumber handles trickier layouts.
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = pdf.pages[:max_pages]
            text = "\n".join((pg.extract_text() or "") for pg in pages)
    except Exception:
        text = ""
    return text


# ===========================================================================
# TIER 1 — API CONNECTORS (JSON)
# ===========================================================================


class CISAKEVConnector(Connector):
    """CISA Known Exploited Vulnerabilities catalog (public JSON feed)."""

    name = "cisa_kev"
    agency = "CISA"
    domain = "Cybersecurity & Defense"
    tier = "api"
    URL = (
        "https://www.cisa.gov/sites/default/files/feeds/"
        "known_exploited_vulnerabilities.json"
    )

    def fetch(self, limit: int = 10) -> list[RawDocument]:
        data = _get(self.URL).json()
        vulns = data.get("vulnerabilities", [])
        # Newest additions first.
        vulns = sorted(vulns, key=lambda v: v.get("dateAdded", ""), reverse=True)
        docs: list[RawDocument] = []
        for v in vulns[:limit]:
            text = (
                f"{v.get('vulnerabilityName', '')}. "
                f"Vendor: {v.get('vendorProject', '')}. "
                f"Product: {v.get('product', '')}. "
                f"{v.get('shortDescription', '')} "
                f"Required action: {v.get('requiredAction', '')} "
                f"Due date: {v.get('dueDate', '')}."
            )
            docs.append(
                RawDocument(
                    source_id=v["cveID"],
                    title=f"{v['cveID']}: {v.get('vulnerabilityName', '')}",
                    url=f"https://nvd.nist.gov/vuln/detail/{v['cveID']}",
                    agency=self.agency,
                    domain=self.domain,
                    raw_text=text,
                    source_format="json",
                    connector=self.name,
                    published_date=v.get("dateAdded"),
                )
            )
        return docs


def _nvd_severity(cve: dict[str, Any]) -> str:
    """Pull a human-readable CVSS severity from an NVD cve object."""
    metrics = cve.get("metrics", {})
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(key)
        if entries:
            data = entries[0].get("cvssData", {})
            score = data.get("baseScore")
            sev = data.get("baseSeverity") or entries[0].get("baseSeverity")
            if score is not None:
                return f"{sev or 'UNKNOWN'} ({score})"
    return "UNSCORED"


class NVDConnector(Connector):
    """NIST National Vulnerability Database — CVE details (REST API 2.0)."""

    name = "nvd"
    agency = "NIST NVD"
    domain = "Cybersecurity & Defense"
    tier = "api"
    URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

    def fetch(self, limit: int = 5) -> list[RawDocument]:
        headers: dict[str, str] = {}
        if settings.NVD_API_KEY:
            headers["apiKey"] = settings.NVD_API_KEY
        # Fetch recent CVEs (NVD returns results in CVE-id order otherwise, which
        # would surface 1980s-90s entries). A publication-date window gives us
        # current vulnerabilities, which is what the business cares about.
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=14)
        fmt = "%Y-%m-%dT%H:%M:%S.000"
        params = {
            "resultsPerPage": limit,
            "startIndex": 0,
            "pubStartDate": start.strftime(fmt),
            "pubEndDate": end.strftime(fmt),
        }
        data = _get(self.URL, params=params, headers=headers).json()
        docs: list[RawDocument] = []
        for item in data.get("vulnerabilities", []):
            cve = item.get("cve", {})
            cid = cve.get("id", "")
            desc = next(
                (d["value"] for d in cve.get("descriptions", []) if d.get("lang") == "en"),
                "",
            )
            sev = _nvd_severity(cve)
            text = f"{cid}. Severity: {sev}. {desc}"
            docs.append(
                RawDocument(
                    source_id=cid,
                    title=f"{cid} — {sev}",
                    url=f"https://nvd.nist.gov/vuln/detail/{cid}",
                    agency=self.agency,
                    domain=self.domain,
                    raw_text=text,
                    source_format="json",
                    connector=self.name,
                    published_date=cve.get("published"),
                )
            )
        return docs


class FederalRegisterConnector(Connector):
    """Federal Register documents API (covers CMS, ONC/HHS, DoD rules, ...)."""

    tier = "api"
    URL = "https://www.federalregister.gov/api/v1/documents.json"

    def __init__(
        self,
        agency_slug: str = "centers-for-medicare-medicaid-services",
        agency: str = "CMS",
        domain: str = "Health IT & Civilian",
        name: str | None = None,
    ) -> None:
        self.agency_slug = agency_slug
        self.agency = agency
        self.domain = domain
        # Unique connector name so --source and logs can tell agencies apart.
        self.name = name or f"fr_{agency_slug.split('-')[0]}"

    def fetch(self, limit: int = 5) -> list[RawDocument]:
        params: list[tuple[str, Any]] = [
            ("per_page", limit),
            ("order", "newest"),
            ("conditions[agencies][]", self.agency_slug),
        ]
        for f in (
            "document_number",
            "title",
            "abstract",
            "html_url",
            "publication_date",
            "type",
            "agencies",
        ):
            params.append(("fields[]", f))
        data = _get(self.URL, params=params).json()
        docs: list[RawDocument] = []
        for r in data.get("results", []):
            agencies = r.get("agencies") or []
            agency_name = agencies[0].get("name") if agencies else self.agency
            body = r.get("abstract") or r.get("title", "")
            text = f"[{r.get('type', '')}] {r.get('title', '')}. {body}"
            docs.append(
                RawDocument(
                    source_id=r.get("document_number", r.get("html_url", "")),
                    title=r.get("title", ""),
                    url=r.get("html_url", ""),
                    agency=agency_name,
                    domain=self.domain,
                    raw_text=text,
                    source_format="json",
                    connector=self.name,
                    published_date=r.get("publication_date"),
                )
            )
        # The Federal Register API ignores per_page=1 (returns its default page),
        # so always enforce the requested limit on our side.
        return docs[:limit]


class RegulationsGovConnector(Connector):
    """Regulations.gov public comments / dockets API (free API key; DEMO_KEY ok)."""

    name = "regulations_gov"
    agency = "Regulations.gov"
    domain = "Health IT & Civilian"
    tier = "api"
    URL = "https://api.regulations.gov/v4/documents"

    def __init__(self, search_term: str = "health information technology") -> None:
        self.search_term = search_term

    def fetch(self, limit: int = 5) -> list[RawDocument]:
        # API requires page[size] >= 5; we still slice to ``limit`` locally.
        page_size = max(5, min(limit, 20))
        headers = {"X-Api-Key": settings.REGULATIONS_GOV_API_KEY or "DEMO_KEY"}
        params = {
            "filter[searchTerm]": self.search_term,
            "page[size]": page_size,
            "sort": "-postedDate",
        }
        data = _get(self.URL, params=params, headers=headers).json()
        docs: list[RawDocument] = []
        for item in data.get("data", [])[:limit]:
            attrs = item.get("attributes", {})
            doc_id = item.get("id") or attrs.get("documentId") or ""
            title = attrs.get("title") or doc_id
            summary = attrs.get("summary") or attrs.get("subtitle") or ""
            docket = attrs.get("docketId") or ""
            text = (
                f"{title}. Docket: {docket}. "
                f"Document type: {attrs.get('documentType', '')}. {summary}"
            ).strip()
            url = (
                f"https://www.regulations.gov/document/{doc_id}"
                if doc_id
                else "https://www.regulations.gov"
            )
            docs.append(
                RawDocument(
                    source_id=str(doc_id),
                    title=title,
                    url=url,
                    agency=self.agency,
                    domain=self.domain,
                    raw_text=text,
                    source_format="json",
                    connector=self.name,
                    published_date=attrs.get("postedDate") or attrs.get("lastPageDate"),
                )
            )
        return docs


class NIST80053OSCALConnector(Connector):
    """NIST SP 800-53 Rev. 5 control catalog (official OSCAL JSON).

    The full catalog is large (~10 MB / 300+ controls). We sample the first
    ``limit`` controls so the pipeline stays free-tier friendly while still
    representing the Tech Standards domain.
    """

    name = "nist_80053_oscal"
    agency = "NIST"
    domain = "Tech Standards & Safety"
    tier = "api"
    URL = (
        "https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
        "nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json"
    )

    def fetch(self, limit: int = 5) -> list[RawDocument]:
        catalog = _get(self.URL).json().get("catalog", {})
        docs: list[RawDocument] = []
        for group in catalog.get("groups", []):
            family = group.get("title") or group.get("id", "")
            for control in group.get("controls", []):
                if len(docs) >= limit:
                    return docs
                cid = control.get("id", "")
                title = control.get("title", cid)
                parts: list[str] = [f"NIST SP 800-53 control {cid}: {title}. Family: {family}."]
                for part in control.get("parts", []) or []:
                    prose = (part.get("prose") or "").strip()
                    if prose:
                        parts.append(prose)
                    if len(" ".join(parts)) > 2500:
                        break
                text = " ".join(parts)
                docs.append(
                    RawDocument(
                        source_id=f"NIST-800-53-{cid}",
                        title=f"NIST SP 800-53 {cid}: {title}",
                        url="https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final",
                        agency=self.agency,
                        domain=self.domain,
                        raw_text=text,
                        source_format="json",
                        connector=self.name,
                    )
                )
        return docs


class SeedTextConnector(Connector):
    """Load a local seed text file (fallback when a live site blocks scrapers)."""

    tier = "seed"

    def __init__(
        self,
        path: str,
        name: str,
        agency: str,
        domain: str,
        title: str,
        url: str,
    ) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = BASE_DIR / self.path
        self.name = name
        self.agency = agency
        self.domain = domain
        self.title = title
        self.url = url

    def fetch(self, limit: int = 1) -> list[RawDocument]:
        text = self.path.read_text(encoding="utf-8")
        return [
            RawDocument(
                source_id=f"seed:{self.name}",
                title=self.title,
                url=self.url,
                agency=self.agency,
                domain=self.domain,
                raw_text=text,
                source_format="txt",
                connector=self.name,
            )
        ]



# ===========================================================================
# TIER 2 — RSS / FEED CONNECTORS (XML)
# ===========================================================================


class RSSConnector(Connector):
    """Generic RSS/Atom feed connector. Configure with a feed URL + labels."""

    tier = "rss"

    def __init__(self, feed_url: str, name: str, agency: str, domain: str) -> None:
        self.feed_url = feed_url
        self.name = name
        self.agency = agency
        self.domain = domain

    def fetch(self, limit: int = 5) -> list[RawDocument]:
        # Fetch via our shared HTTP client (consistent UA, retries, redirects),
        # then hand the raw XML bytes to feedparser.
        content = _get(self.feed_url).content
        feed = feedparser.parse(content)
        docs: list[RawDocument] = []
        for e in feed.entries[:limit]:
            summary = _html_to_text(e.get("summary", ""))
            text = summary or e.get("title", "")
            docs.append(
                RawDocument(
                    source_id=e.get("id") or e.get("link", ""),
                    title=e.get("title", ""),
                    url=e.get("link", ""),
                    agency=self.agency,
                    domain=self.domain,
                    raw_text=text,
                    source_format="xml",
                    connector=self.name,
                    published_date=e.get("published") or e.get("updated"),
                )
            )
        return docs


# ===========================================================================
# TIER 3 — SCRAPE CONNECTORS (HTML / PDF)
# ===========================================================================


class HTMLScrapeConnector(Connector):
    """Extract the main body text from a web page (used when no API/feed)."""

    tier = "scrape"

    def __init__(self, url: str, name: str, agency: str, domain: str) -> None:
        self.url = url
        self.name = name
        self.agency = agency
        self.domain = domain

    def fetch(self, limit: int = 1) -> list[RawDocument]:
        html = _get(self.url).text
        text = trafilatura.extract(html, include_comments=False) or ""
        # Title from <title> if trafilatura didn't capture one.
        title = ""
        soup = BeautifulSoup(html, "lxml")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        return [
            RawDocument(
                source_id=self.url,
                title=title or self.url,
                url=self.url,
                agency=self.agency,
                domain=self.domain,
                raw_text=text,
                source_format="html",
                connector=self.name,
            )
        ]


class PDFConnector(Connector):
    """Download a PDF and extract its text (used for memos/reports)."""

    tier = "scrape"

    def __init__(
        self,
        url: str,
        name: str,
        agency: str,
        domain: str,
        max_pages: int = 15,
    ) -> None:
        self.url = url
        self.name = name
        self.agency = agency
        self.domain = domain
        self.max_pages = max_pages

    def fetch(self, limit: int = 1) -> list[RawDocument]:
        content = _get(self.url).content
        text = _extract_pdf_text(content, self.max_pages)
        title = self.url.rsplit("/", 1)[-1]
        return [
            RawDocument(
                source_id=self.url,
                title=title,
                url=self.url,
                agency=self.agency,
                domain=self.domain,
                raw_text=text,
                source_format="pdf",
                connector=self.name,
            )
        ]


# ===========================================================================
# Registry / convenience
# ===========================================================================


def default_connectors() -> list[Connector]:
    """Return the full source set from the project outline (all four domains).

    Some official sites (CISA advisory RSS, CMS newsroom, live NASCIO PDF, DoD
    CIO library) block automated clients with HTTP 403. Those are covered by
    equivalent working endpoints where possible (CISA KEV API, Federal Register
    for DoD/HHS, NASCIO seed text, HealthIT.gov HTML).
    """
    return [
        # ----- Cybersecurity & Defense -----
        CISAKEVConnector(),
        NVDConnector(),
        FederalRegisterConnector(
            "defense-department",
            agency="DoD",
            domain="Cybersecurity & Defense",
            name="fr_dod",
        ),
        # NOTE: CISA advisory RSS (cisa.gov/.../all.xml) returns HTTP 403 to
        # automated clients on many networks. CISA coverage comes from cisa_kev.
        # ----- Health IT & Civilian -----
        FederalRegisterConnector(
            "centers-for-medicare-medicaid-services",
            agency="CMS",
            domain="Health IT & Civilian",
            name="fr_cms",
        ),
        # ONC rules publish under HHS in the Federal Register.
        FederalRegisterConnector(
            "health-and-human-services-department",
            agency="HHS/ONC",
            domain="Health IT & Civilian",
            name="fr_hhs_onc",
        ),
        RegulationsGovConnector("health information technology"),
        HTMLScrapeConnector(
            "https://www.healthit.gov/newsroom",
            name="healthit_newsroom",
            agency="ONC/HealthIT",
            domain="Health IT & Civilian",
        ),
        # ----- Tech Standards & Safety -----
        RSSConnector(
            "https://www.nist.gov/news-events/news/rss.xml",
            name="nist_news",
            agency="NIST",
            domain="Tech Standards & Safety",
        ),
        PDFConnector(
            "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf",
            name="nist_csf",
            agency="NIST",
            domain="Tech Standards & Safety",
        ),
        NIST80053OSCALConnector(),
        HTMLScrapeConnector(
            "https://csrc.nist.gov/projects/risk-management",
            name="nist_rmf",
            agency="NIST",
            domain="Tech Standards & Safety",
        ),
        # ----- State & Local Gov -----
        # Live NASCIO PDF is Cloudflare-protected; use the public seed summary.
        SeedTextConnector(
            "data/seeds/nascio_2026_priorities.txt",
            name="nascio_priorities",
            agency="NASCIO",
            domain="State & Local Gov",
            title="NASCIO 2026 State CIO Top Ten Priorities",
            url="https://www.nascio.org/resource/state-cio-top-ten-policy-and-technology-priorities-for-2026/",
        ),
        SeedTextConnector(
            "data/seeds/agency_glossary.txt",
            name="agency_glossary",
            agency="SignalPulse",
            domain="Reference",
            title="Public U.S. Agency Glossary (CMS, ONC, NIST, CISA, NVD, HHS, NASCIO)",
            url="https://www.hhs.gov/",
        ),
    ]


def demo_connectors() -> list[Connector]:
    """Overlapping high-value sources for a richer company demo.

    Focuses on Cyber + NIST + CMS so entities (CISA, CVE, NIST, CMS) recur
    across documents and the knowledge graph densifies. Still includes the
    NASCIO seed (blocked live PDF) and HealthIT fallback scrape.
    """
    by_name = {c.name: c for c in default_connectors()}
    order = [
        "cisa_kev",
        "nvd",
        "fr_dod",
        "fr_cms",
        "fr_hhs_onc",
        "nist_news",
        "nist_csf",
        "nist_80053_oscal",
        "nist_rmf",
        "healthit_newsroom",
        "nascio_priorities",
        "agency_glossary",
    ]
    return [by_name[n] for n in order if n in by_name]


# Named ingest profiles used by ``run_pipeline.py --profile …``
# ``connector_limits`` trims LLM-heavy sources (e.g. NIST SP 800-53 controls)
# so a demo run fits free-tier daily token budgets without thinning cyber/CMS.
INGEST_PROFILES: dict[str, dict] = {
    "demo": {
        "description": (
            "Company-demo corpus: deeper pull on overlapping cyber/NIST/CMS sources "
            "(~15-20 docs/source; NIST 800-53 capped). Best for rich chat + denser graph."
        ),
        "limit": 20,
        "max_chunks": 4,
        "connector_limits": {"nist_80053_oscal": 8},
        "connectors": demo_connectors,
    },
    "weekly": {
        "description": (
            "Weekly refresh profile - same sources/depth as demo; run on a schedule "
            "or manually once a week. Incremental skips leave unchanged docs alone."
        ),
        "limit": 20,
        "max_chunks": 4,
        "connector_limits": {"nist_80053_oscal": 8},
        "connectors": demo_connectors,
    },
    "full": {
        "description": (
            "All default sources (including Regulations.gov) at moderate depth."
        ),
        "limit": 10,
        "max_chunks": 3,
        "connector_limits": {},
        "connectors": default_connectors,
    },
    "smoke": {
        "description": "Tiny run for plumbing checks (2 docs/source).",
        "limit": 2,
        "max_chunks": 2,
        "connector_limits": {},
        "connectors": demo_connectors,
    },
}


def fetch_all(
    connectors: list[Connector] | None = None,
    limit: int = 5,
    connector_limits: dict[str, int] | None = None,
) -> list[RawDocument]:
    """Fetch from every connector; a failing source warns but doesn't stop others."""
    connectors = connectors or default_connectors()
    per_source = connector_limits or {}
    out: list[RawDocument] = []
    for c in connectors:
        src_limit = int(per_source.get(c.name, limit))
        try:
            docs = c.fetch(limit=src_limit)
            out.extend(docs)
            print(f"[ok]   {c.name:<18} {len(docs)} docs")
        except Exception as exc:  # noqa: BLE001 - isolate per-source failures
            print(f"[warn] {c.name:<18} failed: {type(exc).__name__}: {exc}")
    return out
