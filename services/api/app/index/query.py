"""Turn what someone types in the search box into a keyword query.

  delivery steel        both words (in any form: delivered, deliveries)
  "letter of credit"    the exact phrase
  steel OR iron         either word
  -draft                leave out passages with this word
  arbitrat*             words starting with this

Everything typed is quoted before it reaches SQLite, so no search can break
or change the query itself."""

import re
from dataclasses import dataclass, field

_TOKEN = re.compile(r'-?"[^"]*"?|\S+')
_WORD = re.compile(r"[\w'’.-]+", re.UNICODE)


class QueryError(ValueError):
    pass


@dataclass
class Query:
    """Best match ranks passages with all the words first (BM25), but also
    finds those with some of them, so a near miss still shows up."""

    fts: str  # an FTS5 MATCH expression: every word must be there
    fts_any: str = ""  # the same, but any of the words will do (for Best match)
    terms: list[str] = field(default_factory=list)  # for highlighting
    text: str = ""  # for meaning search: the words without operators


def _quote(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def parse(raw: str) -> Query:
    raw = raw.strip()
    if not raw:
        raise QueryError("Type something to search for.")
    if len(raw) > 500:
        raise QueryError("That search is too long. Use fewer words.")
    positive: list[str] = []  # FTS terms, with "OR" between some
    negative: list[str] = []
    terms: list[str] = []
    words: list[str] = []
    pending_or = False
    for tok in _TOKEN.findall(raw):
        if tok == "OR":
            pending_or = bool(positive)
            continue
        neg = tok.startswith("-") and len(tok) > 1
        if neg:
            tok = tok[1:]
        if tok.startswith('"'):
            inner = " ".join(_WORD.findall(tok.strip('"')))
            if not inner:
                continue
            term, shown = _quote(inner), inner
        else:
            prefix = tok.endswith("*")
            parts = _WORD.findall(tok)
            if not parts:
                continue
            inner = " ".join(parts)
            if prefix and len(inner) < 2:
                raise QueryError("Put at least two letters before *.")
            term = _quote(inner) + ("*" if prefix else "")
            shown = inner
        if neg:
            negative.append(term)
            continue
        if pending_or:
            positive.append("OR")
        pending_or = False
        positive.append(term)
        terms.append(shown)
        words.append(shown)
    if not positive:
        raise QueryError("Add at least one word to look for, not only words to leave out.")
    # AND binds tighter than OR in FTS5, matching how people read "a b OR c".
    expr = " ".join(_and_join(positive))
    any_expr = " OR ".join(t for t in positive if t != "OR")
    if negative:
        expr = f"({expr}) NOT ({' OR '.join(negative)})"
        any_expr = f"({any_expr}) NOT ({' OR '.join(negative)})"
    return Query(fts=expr, fts_any=any_expr, terms=terms, text=" ".join(words))


def _and_join(items: list[str]) -> list[str]:
    out: list[str] = []
    for i, t in enumerate(items):
        if i and t != "OR" and out[-1] != "OR":
            out.append("AND")
        out.append(t)
    return out
