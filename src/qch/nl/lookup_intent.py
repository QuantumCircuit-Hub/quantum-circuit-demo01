"""QCH Phase 2D.4: narrow, auditable detection of a DIRECT entity-lookup
question ("Tell me about X", "Find version X", "Does X exist?", ...).

Why this exists: in Phase 2D.3's real-model run the LLM planned "Can you
find the version ID 8e9c9a2?" as an arbitrary single-metric lookup, so
a version QCH knows well came back as MISSING_DATA. For unmistakable
lookup phrasing the service now routes straight to `describe_entity`
without asking the model to pick an operator.

The rule is deliberately a whole-question match, not a keyword test:
after removing ONE leading lookup phrase and a small set of filler words
("the", "version", "id", ...), the ONLY thing left must be a single
identifier. So "Find versions with Toffoli below 1M" (remainder: a
predicate), "Show me the parent of X" ("parent of"), "What is the
Toffoli count of X", "Compare X with V4" and "Which versions branched
from X" never match -- analytical questions keep going to the planner.

This module decides only the OPERATOR. It never decides identity:
whether the identifier exists, is ambiguous, or is a submission without
a version is left entirely to `qch.nl.version_resolver` at the normal
canonicalization boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qch.nl.version_resolver import extract_candidate_version_identifiers

ROUTE_REASON = "explicit_entity_lookup_intent"

_LOOKUP_PREFIX_RE = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+)?"
    r"(?:"
    r"find|describe|look\s*up|inspect|show(?:\s+me)?|display|"
    r"tell\s+me\s+(?:more\s+)?about|"
    r"give\s+me\s+(?:the\s+)?(?:details|information|info)\s+(?:about|on|for)|"
    r"what\s+do\s+you\s+know\s+about|"
    r"what\s+(?:information|info|details)\s+do\s+you\s+have\s+(?:about|on|for)"
    r")\b",
    re.IGNORECASE,
)
_EXISTS_RE = re.compile(r"^(?:does|do)\s+(?P<body>.+?)\s+exists?$", re.IGNORECASE)

_FILLER_WORDS = {"the", "a", "an", "version", "submission", "entity", "circuit", "id", "identifier", "key"}

# A token that is not an extracted candidate still counts as an explicit
# identifier (so an unknown one gets an honest UNKNOWN_ENTITY) only if it
# is identifier-SHAPED: one word containing a letter AND a digit, ':',
# '-' or '_'. Plain words ("it", "yourself", "QCH") and bare numbers never do.
_IDENTIFIER_SHAPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


@dataclass(frozen=True)
class LookupIntent:
    identifier: str
    route_reason: str = ROUTE_REASON


def _strip_trailing(text: str) -> str:
    return text.strip().rstrip("?.! ").strip()


def detect_entity_lookup(question: str) -> LookupIntent | None:
    """Returns the single identifier a direct lookup question names, or
    None when the question is not unmistakably a lookup of ONE entity."""
    text = _strip_trailing(question)
    if not text:
        return None

    exists = _EXISTS_RE.match(text)
    if exists:
        remainder = exists.group("body")
    else:
        prefix = _LOOKUP_PREFIX_RE.match(text)
        if prefix is None:
            return None
        remainder = text[prefix.end():]

    words = [w for w in remainder.split() if w.lower() not in _FILLER_WORDS]
    if len(words) != 1:
        return None
    token = words[0].strip(",;\"'")

    candidates = extract_candidate_version_identifiers(question)
    if token in candidates:
        return LookupIntent(token)
    if _IDENTIFIER_SHAPE_RE.match(token) and re.search(r"[0-9:_-]", token) and re.search(r"[A-Za-z]", token):
        return LookupIntent(token)
    return None
