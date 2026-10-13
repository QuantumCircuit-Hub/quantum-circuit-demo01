"""The Version Evolution Explorer's example searches (pure: talks to the store only through `EvolutionExplorer`).

Eight representative versions of the bundled ECDSA.Fail store. The catalog holds an identifier, a title, a
one-sentence description and -- separately -- what the description EXPECTS to be true. Those expectations are
only ever CHECKED against the configured database (`check_examples`, read-only); they are never displayed as
data, so the details panel always shows what the database says. An example is

  available    the version resolves and every expectation holds
  differs      the version resolves but something the description says does not hold (shown, and still openable)
  ambiguous    the identifier matches several versions (the ordinary selection list is used)
  unavailable  no such version in this database

Examples are a convenience for the bundled store; general search works for any dataset or source.
"""

from __future__ import annotations

from dataclasses import dataclass

from qch.explorer import graph
from qch.explorer.models import DIRECTION_BOTH

SOURCE_PREFIX = "ecdsafail"

STATE_AVAILABLE, STATE_DIFFERS, STATE_AMBIGUOUS, STATE_UNAVAILABLE = "available", "differs", "ambiguous", "unavailable"


@dataclass(frozen=True)
class Expectation:
    """What an example's description takes for granted about the database. Checked, never displayed."""

    neighborhood: tuple[int, int] | None = None  # (nodes, edges) of the default one-hop neighborhood
    official: tuple[int, int, int] | None = None  # (Q, T, S) of the example version itself
    submitter: str | None = None
    note_chars: int | None = None
    lowest_accepted_score: bool = False  # the version has the lowest official score among accepted submissions in this database
    incoming_from: str | None = None  # short key of the source of the incoming edge the example is about
    q_delta: int | None = None
    t_delta: int | None = None
    score_reduction_percent: float | None = None  # compared at 4 decimals
    source_metrics_unavailable: bool = False  # the incoming edge's source has no official metrics
    label: tuple[str, str] | None = None  # author classification (family, change type) linked to that edge
    no_label: bool = False  # no author classification is linked to that edge


@dataclass(frozen=True)
class ExampleSpec:
    key: str  # external_version_key
    title: str
    description: str
    expect: Expectation
    fallback: tuple[str, str] | None = None  # (title, description) to use if `lowest_accepted_score` no longer holds


EXAMPLES: tuple[ExampleSpec, ...] = (
    ExampleSpec(
        "ecdsafail:31f9c58", "Mainline and rejected branches",
        "A low-qubit version with one incoming and one outgoing promoted-sequence edge and two rejected branches.",
        Expectation(neighborhood=(5, 4)),
    ),
    ExampleSpec(
        "ecdsafail:897dda2", "More qubits, fewer Toffolis",
        "The promoted step from 31f9c58: qubits up, Toffolis down, score lower, with the submitter's note and no author label recorded.",
        Expectation(incoming_from="31f9c58", q_delta=171, t_delta=-332069, score_reduction_percent=14.8201, no_label=True),
    ),
    ExampleSpec(
        "ecdsafail:9700396", "Best score in the local accepted records",
        "The lowest official score among accepted submissions in this database (not a claim about any live leaderboard), with a long submitter note.",
        Expectation(official=(1258, 904108, 1137367864), submitter="jackylee0424", note_chars=8620, lowest_accepted_score=True),
        fallback=("Long submitter note and a large neighborhood", "A version with a long submitter note and many neighbors, so the node limit and the note viewer are both in play."),
    ),
    ExampleSpec(
        "ecdsafail:0780f67", "Windowing technique",
        "The incoming step from cc5584e with the authors' Windowing classification, shown apart from the submitter's own statement.",
        Expectation(incoming_from="cc5584e", label=("Windowing", "K2 transcript pair-compressor")),
    ),
    ExampleSpec(
        "ecdsafail:9bcc27d", "Fusion with a large Toffoli reduction",
        "The incoming step from 1c400f2: same qubits, far fewer Toffolis; the authors classify it as Fusion.",
        Expectation(incoming_from="1c400f2", q_delta=0, t_delta=-263736, score_reduction_percent=6.9215, label=("Fusion", "boundary cswap-merge")),
    ),
    ExampleSpec(
        "ecdsafail:d19dbb5", "Fewer qubits with more Toffolis",
        "The promoted step from 3bbccd3: fewer qubits, more Toffolis, lower score; the authors classify it as Register and Scratch Allocation.",
        Expectation(incoming_from="3bbccd3", q_delta=-398, t_delta=114202, score_reduction_percent=11.9467, label=("Register and Scratch Allocation", "in-place mutation")),
    ),
    ExampleSpec(
        "ecdsafail:81ff75b", "Measurement-based uncompute",
        "The incoming step from e33b400: same qubits, slightly fewer Toffolis; the authors classify it as Uncompute Strategy.",
        Expectation(incoming_from="e33b400", q_delta=0, t_delta=-8123, label=("Uncompute Strategy", "measurement uncompute")),
    ),
    ExampleSpec(
        "ecdsafail:6f7c159", "Baseline and missing predecessor metrics",
        "The first baseline, whose incoming edge from ca1bf33 has a source without official metrics, so no change can be computed.",
        Expectation(official=(2715, 3960753, 10753444395), incoming_from="ca1bf33", source_metrics_unavailable=True),
    ),
)


@dataclass(frozen=True)
class ExampleStatus:
    spec: ExampleSpec
    state: str
    title: str  # the spec's title, or its fallback title when a claim no longer holds
    description: str
    version_id: str | None = None
    suggested_edge_id: str | None = None  # the incoming edge the example is about, when found
    suggested_edge_source: str | None = None  # its source's display key
    problems: tuple[str, ...] = ()

    @property
    def openable(self) -> bool:
        return self.state in (STATE_AVAILABLE, STATE_DIFFERS, STATE_AMBIGUOUS)


def _source_key(spec: ExampleSpec, short: str) -> str:
    prefix = spec.key.split(":", 1)[0] if ":" in spec.key else SOURCE_PREFIX
    return f"{prefix}:{short}"


def check_example(explorer, spec: ExampleSpec) -> ExampleStatus:
    """Resolves one example and verifies its expectations against the configured database (read-only)."""
    exact = [m for m, _ in explorer.search(spec.key).matches if m.external_version_key == spec.key]
    if not exact:
        return ExampleStatus(spec, STATE_UNAVAILABLE, spec.title, spec.description, problems=(f"{spec.key} is not in this database.",))
    if len(exact) > 1:
        return ExampleStatus(spec, STATE_AMBIGUOUS, spec.title, spec.description, problems=(f"{spec.key} matches {len(exact)} versions.",))
    summary, expect = exact[0], spec.expect
    problems: list[str] = []
    title, description = spec.title, spec.description

    if expect.neighborhood is not None:
        view = graph.project(explorer.start(summary.version_id, direction=DIRECTION_BOTH, relation_types=None))
        if (len(view.nodes), len(view.edges)) != expect.neighborhood:
            problems.append(f"neighborhood is {len(view.nodes)} nodes / {len(view.edges)} edges, expected {expect.neighborhood[0]} / {expect.neighborhood[1]}")
    if expect.official is not None:
        actual = (summary.official.peak_qubits, summary.official.avg_executed_toffoli, summary.official.score)
        if actual != expect.official:
            problems.append("official Q/T/S differ from the example's description")
    if expect.submitter is not None or expect.note_chars is not None:
        detail = explorer.version_detail(summary.version_id)
        if expect.submitter is not None and (detail.submitter is None or detail.submitter.handle != expect.submitter):
            problems.append(f"submitter is not {expect.submitter}")
        if expect.note_chars is not None:
            latest = next((d for d in detail.source_details if d.is_latest), None)
            if latest is None or latest.note_chars != expect.note_chars:
                problems.append(f"latest note is not {expect.note_chars:,} characters")
    if expect.lowest_accepted_score:
        best = explorer.lowest_official_score("accepted")
        if best is None or best.version_id != summary.version_id:
            if spec.fallback:
                title, description = spec.fallback
            else:
                problems.append("it no longer has the lowest official score among accepted submissions")

    edge_id = edge_source = None
    if expect.incoming_from is not None:
        wanted = _source_key(spec, expect.incoming_from)
        edges = [e for e in explorer.edges_for(summary.version_id, "in", None)
                 if explorer.get_versions([e.source_version_id])[e.source_version_id].external_version_key == wanted]
        if len(edges) != 1:
            problems.append(f"expected exactly one incoming edge from {wanted}, found {len(edges)}")
        else:
            edge_id, edge_source = edges[0].edge_id, expect.incoming_from
            detail = explorer.edge_detail(edge_id)
            official = detail.official
            if expect.source_metrics_unavailable and official is not None:
                problems.append("the incoming edge's source now has official metrics")
            if expect.q_delta is not None or expect.t_delta is not None or expect.score_reduction_percent is not None:
                if official is None:
                    problems.append("the incoming edge has no official comparison")
                else:
                    if expect.q_delta is not None and official.q_delta != expect.q_delta:
                        problems.append("ΔQ differs from the example's description")
                    if expect.t_delta is not None and official.t_delta != expect.t_delta:
                        problems.append("ΔT differs from the example's description")
                    if expect.score_reduction_percent is not None and (
                            official.score_reduction_percent is None or round(official.score_reduction_percent, 4) != expect.score_reduction_percent):
                        problems.append("score reduction differs from the example's description")
            found = {(t.family, t.change_type) for t in detail.linked_labels}
            if expect.label is not None and expect.label not in found:
                problems.append(f"author classification {expect.label[0]} / {expect.label[1]} is not linked to that edge")
            if expect.no_label and found:
                problems.append("an author classification is now linked to that edge")
    state = STATE_DIFFERS if problems else STATE_AVAILABLE
    return ExampleStatus(spec, state, title, description, summary.version_id, edge_id, edge_source, tuple(problems))


def check_examples(explorer, specs: tuple[ExampleSpec, ...] | None = None) -> list[ExampleStatus]:
    """Checks every example (the catalog, looked up at call time so a test can substitute its own)."""
    return [check_example(explorer, spec) for spec in (EXAMPLES if specs is None else specs)]