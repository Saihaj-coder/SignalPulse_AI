"""Practical demo / eval questions for SignalPulse AI.

These are the kinds of questions a company employee (security, compliance,
delivery, capture) would ask — not artificial \"graph sightseeing\" prompts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    category: str
    question: str
    # Rough expectation for humans reviewing results (not enforced by code).
    expect: str  # "answer" | "partial" | "refuse"


# 15 practical questions grounded in our public-sector corpus.
EVAL_QUESTIONS: list[EvalQuestion] = [
    EvalQuestion(
        "q01",
        "Cyber / KEV",
        "Do we have any newly listed known-exploited vulnerabilities we should prioritize this week?",
        "answer",
    ),
    EvalQuestion(
        "q02",
        "Cyber / KEV",
        "What is CVE-2026-56291, what product does it affect, and what's the remediation deadline?",
        "answer",
    ),
    EvalQuestion(
        "q03",
        "Cyber / KEV",
        "Are there CISA-required actions or BOD guidance tied to the latest KEV entries?",
        "answer",
    ),
    EvalQuestion(
        "q04",
        "Cyber / CVE",
        "Which recent CVEs in our sources look relevant to web apps or file-upload vulnerabilities?",
        "answer",
    ),
    EvalQuestion(
        "q05",
        "NIST / Risk",
        "According to NIST, how should we structure cybersecurity risk management for a federal or state project?",
        "answer",
    ),
    EvalQuestion(
        "q06",
        "NIST / CSF",
        "What does NIST CSF 2.0 say organizations should use it for?",
        "answer",
    ),
    EvalQuestion(
        "q07",
        "NIST / 800-53",
        "What does NIST SP 800-53 say about access-control policy and account management?",
        "answer",
    ),
    EvalQuestion(
        "q08",
        "NIST / RMF",
        "Where can I find NIST's Risk Management Framework overview in our sources?",
        "answer",
    ),
    EvalQuestion(
        "q09",
        "Health / CMS",
        "Are there any recent CMS Medicare/Medicaid program notices we should be aware of?",
        "answer",
    ),
    EvalQuestion(
        "q10",
        "Health / ONC",
        "What's in the latest HealthIT.gov / ONC news about health information technology programs or awards?",
        "answer",
    ),
    EvalQuestion(
        "q11",
        "Health / FDA",
        "Is there anything recent on FDA digital health technologies in clinical investigations?",
        "answer",
    ),
    EvalQuestion(
        "q12",
        "Health / HHS",
        "Any new HHS/FDA drug establishment registration or listing requirements in the Federal Register?",
        "answer",
    ),
    EvalQuestion(
        "q13",
        "Defense / Privacy",
        "Are there recent Defense Department Privacy Act or system-of-records notices that could affect DoD work?",
        "answer",
    ),
    EvalQuestion(
        "q14",
        "State / NASCIO",
        "What are state CIOs prioritizing in 2026 according to NASCIO?",
        "answer",
    ),
    EvalQuestion(
        "q15",
        "State / NASCIO",
        "Is AI or cybersecurity ranked as a top state CIO priority this year?",
        "answer",
    ),
]

# Extra guardrail checks (should refuse).
REFUSAL_QUESTIONS: list[EvalQuestion] = [
    EvalQuestion(
        "r01",
        "Guardrail",
        "What is the capital of France?",
        "refuse",
    ),
    EvalQuestion(
        "r02",
        "Guardrail",
        "What was our company's internal Q3 revenue last year?",
        "refuse",
    ),
]
