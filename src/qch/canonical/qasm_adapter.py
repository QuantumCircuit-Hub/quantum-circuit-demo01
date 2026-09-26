"""QasmStructuralParser: OpenQASM 2 text -> CanonicalCircuit (DB-3
Phase A7).

Reuses the same supported-subset understanding as
`qch.importers.qasm_parsing` (version pragma, one `include`, `gate`/
`opaque` declarations, `qreg`/`creg`, flat gate applications, `barrier`,
`measure`) but is a SEPARATE, richer parser: `qasm_parsing.py` only
counts statements (by design, for `QASMImporter`'s lightweight
metrics); this module extracts each operation's actual qubit/classical/
parameter OPERANDS, because structural identity requires knowing WHICH
qubits an operation touches, not just how many operations of each kind
exist. Kept as its own module rather than extending
`qasm_parsing.ParsedQasm` so that module's existing, already-tested
behavior (and `QASMImporter`'s use of it) is completely undisturbed.
"""

from __future__ import annotations

import re

from qch.canonical import CanonicalCircuit, CanonicalOperation

_QREG_RE = re.compile(r"^qreg\s+(\w+)\s*\[\s*(\d+)\s*\]$")
_CREG_RE = re.compile(r"^creg\s+(\w+)\s*\[\s*(\d+)\s*\]$")
_GATE_KEYWORD_RE = re.compile(r"\bgate\b")
_MEASURE_RE = re.compile(r"^measure\s+(\w+)\s*\[\s*(\d+)\s*\]\s*->\s*(\w+)\s*\[\s*(\d+)\s*\]$")
_INDEXED_OPERAND_RE = re.compile(r"^(\w+)\[(\d+)\]$")
_NON_OPERATION_KEYWORDS = {"OPENQASM", "include", "opaque"}


class QasmParseError(ValueError):
    pass


class QasmStructuralParser:
    format = "qasm2"

    def can_parse(self, artifact_format: str | None) -> bool:
        return artifact_format is not None and artifact_format.lower() in ("qasm2", "qasm", "openqasm2")

    def parse(self, content: bytes) -> CanonicalCircuit:
        source = content.decode("utf-8")
        source = _strip_gate_definitions(source)

        qubit_index: dict[tuple[str, int], int] = {}  # (register, position) -> dense global index
        classical_index: dict[tuple[str, int], int] = {}
        operations: list[CanonicalOperation] = []

        for raw_statement in source.split(";"):
            statement = _strip_comment_and_normalize_whitespace(raw_statement)
            if not statement:
                continue

            keyword = _leading_keyword(statement)
            if keyword in _NON_OPERATION_KEYWORDS:
                continue

            qreg_match = _QREG_RE.match(statement)
            if qreg_match:
                register, size = qreg_match.group(1), int(qreg_match.group(2))
                for position in range(size):
                    qubit_index[(register, position)] = len(qubit_index)
                continue

            creg_match = _CREG_RE.match(statement)
            if creg_match:
                register, size = creg_match.group(1), int(creg_match.group(2))
                for position in range(size):
                    classical_index[(register, position)] = len(classical_index)
                continue

            measure_match = _MEASURE_RE.match(statement)
            if measure_match:
                q_reg, q_pos, c_reg, c_pos = measure_match.group(1), int(measure_match.group(2)), measure_match.group(3), int(measure_match.group(4))
                qubit = _resolve(qubit_index, q_reg, q_pos, statement)
                bit = _resolve(classical_index, c_reg, c_pos, statement)
                operations.append(
                    CanonicalOperation(index=len(operations), name="measure", qubits=(qubit,), classical_bits=(bit,))
                )
                continue

            # A gate application or barrier: "<name>[(<params>)] <operand>[,<operand>...]"
            name, params_text, operands_text = _split_operation(statement)
            qubits = tuple(_resolve(qubit_index, *_parse_operand(operand), statement) for operand in operands_text)
            parameters = _parse_parameters(params_text)
            operations.append(CanonicalOperation(index=len(operations), name=name.lower(), qubits=qubits, parameters=parameters))

        return CanonicalCircuit(
            qubit_count=len(qubit_index),
            classical_bit_count=len(classical_index),
            operations=tuple(operations),
        )


def _resolve(index_map: dict[tuple[str, int], int], register: str, position: int, statement: str) -> int:
    key = (register, position)
    if key not in index_map:
        raise QasmParseError(f"operand {register}[{position}] used before declaration in statement: {statement!r}")
    return index_map[key]


def _parse_operand(token: str) -> tuple[str, int]:
    match = _INDEXED_OPERAND_RE.match(token)
    if not match:
        raise QasmParseError(f"expected an indexed operand like q[0], got {token!r}")
    return match.group(1), int(match.group(2))


def _split_operation(statement: str) -> tuple[str, str | None, list[str]]:
    """`"cp(pi/2) q[4],q[3]"` -> `("cp", "pi/2", ["q[4]", "q[3]"])`;
    `"cp( pi / 2 ) q[4],q[3]"` (extra internal whitespace) -> the same;
    `"h q[4]"` -> `("h", None, ["q[4]"])`. Finds the parenthesized
    parameter block by actual paren position, not by splitting on the
    first space -- a space immediately after `(` or before `)` would
    otherwise be mistaken for the name/operand separator."""
    paren_start = statement.find("(")
    space_pos = statement.find(" ")
    if paren_start != -1 and (space_pos == -1 or paren_start < space_pos):
        name = statement[:paren_start]
        paren_end = statement.find(")", paren_start)
        if paren_end == -1:
            raise QasmParseError(f"unterminated parameter list in statement: {statement!r}")
        params_text = statement[paren_start + 1 : paren_end]
        operands_part = statement[paren_end + 1 :].strip()
        operands_text = [o.strip() for o in operands_part.split(",") if o.strip()] if operands_part else []
        return name, params_text, operands_text

    head, _, operands_part = statement.partition(" ")
    operands_text = [o.strip() for o in operands_part.split(",") if o.strip()] if operands_part else []
    return head, None, operands_text


def _parse_parameters(params_text: str | None) -> tuple[str, ...]:
    """Canonicalization rule 4 (see qch.canonical's own docstring):
    literal text, whitespace stripped, NEVER arithmetically evaluated."""
    if not params_text:
        return ()
    return tuple("".join(p.split()) for p in params_text.split(","))


def _strip_gate_definitions(source: str) -> str:
    """Identical logic to `qch.importers.qasm_parsing._strip_gate_definitions`
    -- deliberately duplicated rather than imported, since that
    function is a private implementation detail of a module this one
    must not depend on (keeping the two parsers fully independent, per
    this module's own docstring)."""
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
        position = cursor + 1
    return "".join(pieces)


def _strip_comment_and_normalize_whitespace(raw_statement: str) -> str:
    without_comment = re.sub(r"//.*", "", raw_statement)
    return " ".join(without_comment.split())


def _leading_keyword(statement: str) -> str:
    first_token = statement.split(" ", 1)[0]
    first_token = first_token.split("[", 1)[0]
    first_token = first_token.split("(", 1)[0]
    return first_token
