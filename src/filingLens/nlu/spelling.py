"""Close misspellings of covered company names: "wifpro" -> "wipro", "infossys" -> "infosys".

A word is corrected only when it is not an ordinary question or finance word, is at least 4
letters long, starts with the same letter as a word of a covered company's name or alias, and
is at least 80% similar to it (difflib). Short names (TCS, HUL) are never guessed at: a 3-letter
typo is as likely to be another company. The corrected question is used everywhere (LLM,
rules, company checks), and the answer says how the word was read.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from filingLens.domain.registry import Registry

MIN_LEN = 4
CUTOFF = 0.8
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9&\-]*")


@dataclass(frozen=True)
class Correction:
    typed: str
    read_as: str


def company_words(reg: Registry) -> set[str]:
    """Distinctive words of covered companies' names, aliases and tickers."""
    words: set[str] = set()
    for t, c in reg.companies.items():
        for phrase in (t, c.name, *c.aliases):
            words.update(w for w in re.findall(r"[a-z0-9&\-]+", phrase.lower())
                         if len(w) >= MIN_LEN and w not in {"limited", "ltd", "india",
                                                             "industries", "services"})
    return words


def correct_companies(question: str, reg: Registry,
                      common: frozenset[str] = frozenset()) -> tuple[str, list[Correction]]:
    """The question with close misspellings of company words fixed, and what was changed.
    ``common``: words never corrected (question words, metric names, stopwords)."""
    vocab = company_words(reg)
    if not vocab:
        return question, []
    fixes: dict[str, str] = {}
    for m in _WORD.finditer(question):
        w = m.group(0).lower()
        if len(w) < MIN_LEN or w in vocab or w in common or w in fixes:
            continue
        same_start = [v for v in vocab if v[0] == w[0]]
        best = difflib.get_close_matches(w, same_start, n=1, cutoff=CUTOFF)
        if best:
            fixes[w] = best[0]
    if not fixes:
        return question, []
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, fixes)) + r")\b", re.IGNORECASE)
    fixed = pattern.sub(lambda m: fixes[m.group(0).lower()], question)
    return fixed, [Correction(k, v) for k, v in fixes.items()]
