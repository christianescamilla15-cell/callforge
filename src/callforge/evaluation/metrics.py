"""Deterministic RAG metrics: retrieval (hit@k, MRR, citation coverage) and answer checks.

Answer checks need no LLM:
* faithfulness — every claim (declarative sentence with content) must be backed by the fragments the
  answer CITES: enough of its content stems appear in them and every number it states appears there;
* citation validity — cited numbers must point to fragments that were actually shown (no phantoms);
* exactness against the reference — key facts present (folded substring) and token F1.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

from callforge.evaluation.dataset import fold

# Function words that carry no claim (Spanish + English). Kept here, independent of the store's list:
# the metric must not move if someone tunes the retriever's tokenizer.
_STOPWORDS = frozenset("""
a al algo ante como con contra cual cuando de del desde donde e el ella ellas ellos en entre era es esa
ese eso esta este esto estos estas fue ha hay la las le les lo los mas me mi mis muy no nos o para pero
por que se ser si sin sobre son su sus tambien te tiene tienen tu tus un una unas uno unos y ya yo
puede puedes pueden debe debes deben solo cada otro otra hasta usted ustedes nuestro nuestra
the a an and or of to in on for is are be it its this that with your you can do does not
""".split())
_TOKEN = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_STEM = 5


def content_stems(text: str) -> list[str]:
    """Content tokens, folded and cut to a 5-letter prefix (cheap stemming: «reinicia» ~ «reiniciar»)."""
    stems = []
    for token in _TOKEN.findall(fold(text)):
        if token in _STOPWORDS or (len(token) < 2 and not token.isdigit()):
            continue
        stems.append(token if token.isdigit() else token[:_STEM])
    return stems


def numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUMBER.findall(text)}


# --------------------------------------------------------------------------- #
# Retrieval
# --------------------------------------------------------------------------- #


def hit_at_k(ranked: Sequence[str], relevant: Collection[str], k: int) -> float:
    return 1.0 if any(doc in relevant for doc in ranked[:k]) else 0.0


def reciprocal_rank(ranked: Sequence[str], relevant: Collection[str]) -> float:
    for position, doc in enumerate(ranked, start=1):
        if doc in relevant:
            return 1.0 / position
    return 0.0


def citation_coverage(ranked: Sequence[str], relevant: Collection[str], k: int) -> float:
    """Share of the sources an answer needs that are among the k fragments it can cite."""
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & set(relevant)) / len(set(relevant))


# --------------------------------------------------------------------------- #
# Answers
# --------------------------------------------------------------------------- #

ABSTENTION_MARKERS = (
    "no encontre", "no tengo informacion", "no tengo esa informacion", "no cuento con",
    "no hay informacion", "no dispongo", "no tengo datos", "no aparece en la base",
    "la base de conocimiento no", "no se menciona", "i don't have", "i do not have",
)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


def claims(reply: str) -> list[str]:
    """Declarative sentences with at least three content stems (greetings and questions are not claims)."""
    return [s for s in split_sentences(reply) if not s.endswith("?") and len(set(content_stems(s))) >= 3]


def is_abstention(reply: str, citations: Sequence[int]) -> bool:
    folded = fold(reply)
    return not citations and any(marker in folded for marker in ABSTENTION_MARKERS)


@dataclass
class ClaimCheck:
    text: str
    coverage: float
    missing_numbers: list[str]
    supported: bool


@dataclass
class AnswerCheck:
    claims: list[ClaimCheck] = field(default_factory=list)
    cited: list[int] = field(default_factory=list)
    phantom_citations: list[int] = field(default_factory=list)
    abstained: bool = False

    @property
    def faithfulness(self) -> float | None:
        """Supported claims / claims; None when there is nothing to check (abstention or no claims)."""
        if self.abstained or not self.claims:
            return None
        return sum(c.supported for c in self.claims) / len(self.claims)


def verify_answer(reply: str, citations: Sequence[int], fragments: Sequence[str],
                  min_coverage: float = 0.6) -> AnswerCheck:
    """Check each claim of `reply` against the union of the fragments it cites (1-based)."""
    cited = sorted({c for c in citations if isinstance(c, int)})
    valid = [c for c in cited if 1 <= c <= len(fragments)]
    check = AnswerCheck(cited=cited, phantom_citations=[c for c in cited if c not in valid],
                        abstained=is_abstention(reply, cited))
    if check.abstained:
        return check
    evidence_text = "\n".join(fragments[c - 1] for c in valid)
    evidence = set(content_stems(evidence_text))
    evidence_numbers = numbers(evidence_text)
    for sentence in claims(reply):
        stems = set(content_stems(sentence))
        coverage = len(stems & evidence) / len(stems)
        missing = sorted(numbers(sentence) - evidence_numbers)
        check.claims.append(ClaimCheck(text=sentence, coverage=round(coverage, 4), missing_numbers=missing,
                                       supported=coverage >= min_coverage and not missing))
    return check


def key_fact_recall(reply: str, key_facts: Sequence[str]) -> float:
    if not key_facts:
        return 0.0
    folded = fold(reply)
    return sum(fold(fact) in folded for fact in key_facts) / len(key_facts)


def token_f1(reply: str, reference: str) -> float:
    predicted, gold = Counter(content_stems(reply)), Counter(content_stems(reference))
    common = sum((predicted & gold).values())
    if common == 0:
        return 0.0
    precision, recall = common / sum(predicted.values()), common / sum(gold.values())
    return 2 * precision * recall / (precision + recall)
