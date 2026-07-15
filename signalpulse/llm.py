"""Shared LLM client for SignalPulse AI.

One place to build chat models so entity extraction and the agentic RAG chatbot
use the same configuration and the same **primary + fallback** strategy:

    primary   = whatever ``settings.LLM_PROVIDER`` is (default Groq)
    fallbacks = other configured providers, in a fixed preference order

Configured providers (when their API keys are set):
    groq, gemini, mistral, deepseek

Failover uses LangChain ``.with_fallbacks(...)`` for extraction. The agent
tries providers in order inside ``ask()`` (tool binding needs a concrete model).
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from signalpulse.config import settings

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.rate_limiters import InMemoryRateLimiter

# Preference after the primary: cheap/fast free tiers first, then deeper models.
_FALLBACK_ORDER = ("groq", "mistral", "deepseek", "gemini")


@lru_cache(maxsize=1)
def _groq_rate_limiter() -> "InMemoryRateLimiter":
    """A shared, conservative limiter to stay under Groq's free-tier limits.

    Free tier caps requests-per-minute and tokens-per-minute. We throttle to a
    steady ~24 requests/min with small bursts, which (together with retries)
    prevents rate-limit crashes during bulk extraction.
    """
    from langchain_core.rate_limiters import InMemoryRateLimiter

    return InMemoryRateLimiter(
        requests_per_second=0.4,  # ~24 requests/minute
        check_every_n_seconds=0.1,
        max_bucket_size=5,  # allow short bursts
    )


def _provider_ready(name: str) -> bool:
    return {
        "gemini": settings.gemini_ready,
        "groq": settings.groq_ready,
        "mistral": settings.mistral_ready,
        "deepseek": settings.deepseek_ready,
    }.get(name.lower(), False)


def available_providers() -> list[str]:
    """Return configured providers, primary first (per ``LLM_PROVIDER``)."""
    preferred = settings.LLM_PROVIDER.lower().strip()
    rest = [p for p in _FALLBACK_ORDER if p != preferred]
    order = [preferred] + rest if preferred else list(_FALLBACK_ORDER)
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for p in order:
        if p in seen:
            continue
        seen.add(p)
        if _provider_ready(p):
            out.append(p)
    return out


def get_chat_model(
    provider: str, *, temperature: float = 0.0, max_retries: int = 1
) -> "BaseChatModel":
    """Build a single LangChain chat model for the given provider.

    ``max_retries`` is kept low so that when a provider is rate-limited we fail
    over to the next provider quickly instead of blocking on long backoffs.
    """
    provider = provider.lower()
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            google_api_key=settings.GOOGLE_API_KEY,
            temperature=temperature,
            max_retries=max_retries,
        )
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=settings.GROQ_MODEL,
            api_key=settings.GROQ_API_KEY,
            temperature=temperature,
            max_retries=max(max_retries, 4),
            rate_limiter=_groq_rate_limiter(),
        )
    if provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(
            model=settings.MISTRAL_MODEL,
            api_key=settings.MISTRAL_API_KEY,
            temperature=temperature,
            max_retries=max_retries,
        )
    if provider == "deepseek":
        from langchain_openai import ChatOpenAI

        # DeepSeek exposes an OpenAI-compatible Chat Completions API.
        return ChatOpenAI(
            model=settings.DEEPSEEK_MODEL,
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            temperature=temperature,
            max_retries=max_retries,
        )
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def is_transient_llm_error(exc: BaseException) -> bool:
    """True for errors that warrant trying the next provider.

    Includes rate limits / quotas, and also auth failures on a single
    optional provider so one bad key does not block the whole chain.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    needles = (
        "ratelimit",
        "rate_limit",
        "rate limit",
        "resourceexhausted",
        "resource exhausted",
        "quota",
        "tokens per day",
        "tpd",
        "too many requests",
        "overloaded",
        "capacity",
        "429",
        "authentication",
        "unauthorized",
        "invalid api key",
        "invalid_api_key",
        "401",
    )
    if any(
        n in name
        for n in ("ratelimit", "resourceexhausted", "quota", "authentication", "auth")
    ):
        return True
    return any(n in text for n in needles)


def get_llm(*, temperature: float = 0.0) -> "BaseChatModel":
    """Return the primary chat model with the fallback chain attached.

    Use this everywhere you just want "the model" and want automatic failover
    (e.g. entity extraction).
    """
    providers = available_providers()
    if not providers:
        raise RuntimeError(
            "No LLM provider configured. Set GROQ_API_KEY, MISTRAL_API_KEY, "
            "DEEPSEEK_API_KEY, and/or GOOGLE_API_KEY in .env"
        )
    primary = get_chat_model(providers[0], temperature=temperature)
    fallbacks = [get_chat_model(p, temperature=temperature) for p in providers[1:]]
    return primary.with_fallbacks(fallbacks) if fallbacks else primary
