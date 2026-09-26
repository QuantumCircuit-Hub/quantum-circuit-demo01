"""A deliberately minimal OpenQASM 2 statement parser.

This is NOT a general OpenQASM parser. It supports exactly the flat,
qelib1.inc-style subset used by this project's existing GHZ-5 and
QFT-entangled-5 examples (data/mqtbench/raw/ghz_5.qasm and
qftentangled_5.qasm): a version pragma, one `include`, an inline
`opaque` declaration or a `gate NAME ... { body }` compound definition
(see `_strip_gate_definitions` below), one or more `qreg`/`creg`
declarations, and a flat sequence of gate applications, `barrier`
statements, and `measure` statements. It does NOT support classical
control (`if`), loops, OpenQASM 3, or nested nonstandard expressions
beyond a gate name plus its argument/parameter list.

If QASMImporter ever needs to support more of the language, extend
this module -- QASM parsing knowledge belongs here, not scattered
through qch's generic core or its services.

Every count this module reports is a literal statement count -- it
never estimates or infers anything (e.g. there is no attempt at circuit
depth, since that requires real dependency analysis this small parser
does not do). In particular, a custom gate's *definition* is recognized
generically (any OpenQASM 2 `gate` block, not any particular gate name)
and treated as a declaration, contributing nothing to operation_count;
a *call* to that gate is then parsed exactly like any other gate
application -- one opaque operation, named after the gate, with no
attempt to decompose or understand what it computes. This is a
syntactic distinction ("can this be counted") not a semantic one
("is this understood") -- see the module docstring's sibling note in
qch/importers/qasm.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_QREG_RE = re.compile(r"^qreg\s+\w+\s*\[\s*(\d+)\s*\]$")
_GATE_KEYWORD_RE = re.compile(r"\bgate\b")

# Statement keywords that are not circuit operations -- version
# pragmas, includes, and classical register/gate declarations. `opaque`
# is OpenQASM 2's other declaration-only keyword (an `opaque name(...)
# qargs;` statement with no body) -- not exercised by this project's
# current circuits, but a real part of the language, so it is treated
# the same as `gate`: a declaration, never an operation.
_NON_OPERATION_KEYWORDS = {"OPENQASM", "include", "creg", "opaque"}


@dataclass
class ParsedQasm:
    """The result of parsing one OpenQASM 2 source string.

    `operation_count` counts every gate application, `barrier`, and
    `measure` statement -- i.e. every statement that is not a version
    pragma, include, or register/gate declaration. `gate_counts` breaks
    that same total down by statement keyword (e.g. "h", "cx", "qft",
    "barrier", "measure"), so `sum(gate_counts.values()) ==
    operation_count`. A custom gate's *definition* is never itself
    counted as an operation, and its body is never decomposed into
    `gate_counts` -- only calls to it are (see module docstring).
    """

    qubit_count: int
    operation_count: int
    gate_counts: dict[str, int] = field(default_factory=dict)


def parse_qasm2(source: str) -> ParsedQasm:
    """Parse a flat OpenQASM 2 source string (see module docstring for
    the supported subset)."""
    source = _strip_gate_definitions(source)

    qubit_count = 0
    operation_count = 0
    gate_counts: dict[str, int] = {}

    for raw_statement in source.split(";"):
        statement = _strip_comment_and_normalize_whitespace(raw_statement)
        if not statement:
            continue

        keyword = _leading_keyword(statement)

        if keyword in _NON_OPERATION_KEYWORDS:
            continue

        if keyword == "qreg":
            match = _QREG_RE.match(statement)
            if match:
                qubit_count += int(match.group(1))
            continue

        # Anything else -- barrier, measure, or a gate application such
        # as "h q[4]" / "cx q[4],q[3]" / "qft q[0],...,q[4]" (built-in
        # or a previously-declared custom gate, indistinguishable here
        # and treated identically) -- is one operation, tallied by its
        # leading keyword.
        operation_count += 1
        gate_counts[keyword] = gate_counts.get(keyword, 0) + 1

    return ParsedQasm(qubit_count=qubit_count, operation_count=operation_count, gate_counts=gate_counts)


def _strip_gate_definitions(source: str) -> str:
    """Remove every OpenQASM 2 `gate <name> <args...> { <body> }`
    compound definition block from `source` entirely (header and body
    both), so the semicolon-delimited statements inside a gate's body
    are never mistaken for top-level circuit operations, and so
    whatever program text follows the block (e.g. a `qreg` declaration)
    parses normally afterwards.

    This is a purely syntactic move: the block is deleted, not
    interpreted. It applies to ANY `gate` block regardless of name --
    nothing here is specific to any one dataset's custom gate.
    Brace-matching is a simple depth counter (sufficient for this
    OpenQASM 2 subset, which does not nest gate definitions inside one
    another). If a `gate` keyword is found with no matching `{`, this
    function stops rewriting and leaves the remainder of the source
    untouched, rather than silently discarding it.
    """
    pieces: list[str] = []
    position = 0
    while True:
        match = _GATE_KEYWORD_RE.search(source, position)
        if not match:
            pieces.append(source[position:])
            break

        brace_start = source.find("{", match.start())
        if brace_start == -1:
            pieces.append(source[position:])
            break

        depth = 0
        cursor = brace_start
        while cursor < len(source):
            if source[cursor] == "{":
                depth += 1
            elif source[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1

        pieces.append(source[position : match.start()])
        position = cursor + 1  # resume just after the matching '}'

    return "".join(pieces)


def _strip_comment_and_normalize_whitespace(raw_statement: str) -> str:
    without_comment = re.sub(r"//.*", "", raw_statement)
    return " ".join(without_comment.split())


def _leading_keyword(statement: str) -> str:
    """The first token of a statement, with any qubit index (e.g.
    "[4]") or parameter list (e.g. "(pi/2)") stripped -- "h" from
    "h q[4]", "qreg" from "qreg q[5]", "cp" from "cp(pi/2) q4,q3"."""
    first_token = statement.split(" ", 1)[0]
    first_token = first_token.split("[", 1)[0]
    first_token = first_token.split("(", 1)[0]
    return first_token
