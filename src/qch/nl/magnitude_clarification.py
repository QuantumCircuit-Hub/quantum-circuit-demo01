"""QCH Phase 2D.7.2.1 (P11): structured choices for an ambiguous change
magnitude ("decreased by 5%": exactly? at least? more than?).

Presentation / round trip only -- the frozen Phase 2D.7.2 grounding
(qch.nl.transition_grounding) and compiler are reused unchanged:

    displayed choice ("At least 5%")
        -> stored rewrite of the bare phrase ("decreased by at least 5%")
        -> the existing TRANSITION_METRIC_FILTER intent (comparator at_least)
        -> the existing deterministic compiler (_pct_change <= -5.0)

Only the two relations the frozen algebra defines are offered. "Exactly X%"
is NOT offered: exact equality is not a defined transition predicate.
"""

from __future__ import annotations

from dataclasses import dataclass

from qch.nl.transition_grounding import _CHANGE_VERB_RE, _MAGNITUDE_RE, split_suffix

UNSUPPORTED_EXACT_NOTE = "An exact percentage match is not a supported QCH transition condition; choose 'at least' or 'more than'."


@dataclass(frozen=True)
class MagnitudeChoice:
    label: str  # shown to the user
    description: str
    comparator: str  # "at_least" | "more_than" (the existing TransitionPredicate comparators)
    rewritten_question: str  # the original question with ONLY the bare magnitude phrase made explicit


def magnitude_choices(question: str) -> list[MagnitudeChoice]:
    """Choices for the FIRST bare magnitude ("<change verb> [by] N[%]" without a
    comparator) in the main question; [] when there is none."""
    main, _ = split_suffix(question)
    for verb in _CHANGE_VERB_RE.finditer(main):
        m = _MAGNITUDE_RE.match(main[verb.end():])
        if not m or m.group("cmp"):
            continue
        start, end = verb.start(), verb.end() + m.end()
        number, unit = m.group("num"), ("%" if m.group("pct") == "%" else (" percent" if m.group("pct") else ""))
        shown = f"{number}%" if m.group("pct") else number
        choices = []
        for comparator, words, relation in (("at_least", "at least", "{} or more"), ("more_than", "more than", "strictly more than {}")):
            phrase = f"{verb.group(0)} by {words} {number}{unit}"
            choices.append(MagnitudeChoice(
                label=f"{words.capitalize()} {shown}",
                description=f"A change of {relation.format(shown)} (\"{phrase}\").",
                comparator=comparator,
                rewritten_question=question[:start] + phrase + question[end:],
            ))
        return choices
    return []
