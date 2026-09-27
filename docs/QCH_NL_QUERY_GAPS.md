# QCH NL Query Gaps

Questions users can reasonably ask that the current QCH Query Engine
(`src/qch/query/operators.py`'s `OPERATORS` registry) cannot express by
composing existing primitives. Recorded here rather than silently
worked around or answered approximately (Phase 2B spec section 24) --
each entry states why composition is insufficient, a possible future
operator/composition, a rough complexity estimate, and the user value,
so a future phase can prioritize among them deliberately.

None of these are implemented in Phase 2A, Phase 2A-2, or Phase 2B.

## 1. Bounded path between two named versions

**Example questions:** "Find the versions between V4 and V5." / "Show
every version between ecdsafail:X and ecdsafail:Y."

**Why current operators can't express it:** `traverse` only walks
outward from ONE version in one direction (`predecessors`/
`successors`/`ancestors`/`descendants`); there is no operator that
takes TWO version identifiers and returns the path (or DAG slice)
between them.

**Possible future composition:** a new primitive `path_between(from,
to, relation_type)` that runs a bounded BFS from `from` looking for
`to`, or -- if bounded to a linear chain like `next_promoted_commit`
-- a filter on `sequence_no` between the two endpoints' sequence
numbers (cheap, but only correct for a non-branching relation type;
`branched_from` is a DAG and doesn't have a single well-defined
"between").

**Estimated complexity:** medium (needs a real design decision about
which direction wins on a branch, and whether "between" means
"reachable via any path" or "on the shortest path").

**User value:** medium -- a natural provenance question, but
answerable today via two separate `traverse` calls plus manual
inspection.

## 2. Dependent / chained queries

**Example questions:** "What happened after the largest structural
Toffoli regression?" / "How many versions did it take to recover after
a regression?"

**Why current operators can't express it:** each needs the RESULT of
one query (e.g. "which transition is the largest regression") fed as
an input identifier into a SECOND, different kind of query (e.g.
"traverse forward from that transition's child version"). Today's
pipeline threads one list-shaped `value` through `compute_delta` /
`filter` / `sort` / `limit`, but has no way to reference "the first
result of the previous stage" as a named identifier for a structurally
different next step.

**Possible future composition:** a `bind` step that names a field from
the current result (e.g. `child_version_id` of the top-ranked
transition) as a plan-scoped variable, usable by a later step's
`version` param -- effectively a very small, explicit "pipe the
result" mechanism, not a general subquery language.

**Estimated complexity:** medium-high (`QCHQueryPlan`/`QCHQueryStep`
would need a way to reference a prior step's output field by name, a
real change to the plan IR, not just a new operator).

**User value:** high -- this is one of the most naturally-phrased
follow-up question shapes ("what happened after that?").

## 3. Recovery-length / trend / run-length analysis

**Example questions:** "How many versions did it take to recover after
the largest regression?" / "What was the longest consecutive run of
structural Toffoli decreases?" / "Show the structural Toffoli trend
across all promoted versions."

**Why current operators can't express it:** these need a SEQUENTIAL
scan over an already-ordered list, tracking running state (e.g. "count
consecutive decreases until the metric exceeds its pre-regression
value again"). Every existing primitive processes each record
independently ; none carries state between records.

**Possible future composition:** a `run_length_scan` primitive taking
a comparator/threshold and returning contiguous run boundaries, or a
`cumulative` primitive computing a running sum/extremum. Both are
genuinely new kinds of primitives, not compositions of existing ones.

**Estimated complexity:** high (state-carrying primitives are a new
category this engine doesn't have yet; also raises open semantic
questions -- e.g. "recovered" relative to which baseline value?).

**User value:** high for a code-evolution-analysis product, but the
semantics need real design work before an implementation is worth
trusting.

## 4. Bare superlative ("best"/"worst") without a schema-backed direction convention

**Discovered during:** Phase 2B multi-turn clarification testing
(`tests/test_qch_nl_service.py`), not a benchmark case.

**Example questions:** "Which version is best?" (already correctly
AMBIGUOUS at the metric level) -- but even once a user names a SPECIFIC
metric (e.g. "structural Toffoli count"), the system still cannot
determine whether "best" means lowest or highest for that metric
without either (a) a schema-declared per-metric "improvement
direction" convention, or (b) asking the user explicitly.

**Why current operators can't express it:** `docs/qch_query_schema.json`
has no `improvement_direction` field on any metric entry today. Adding
one purely to make "best" resolvable is exactly the kind of "modify the
engine merely to make LLM/NL output easier" this project's own scope
rules (Phase 2A section 0, Phase 2B section 2) warn against doing
without a demonstrated need -- and guessing a convention in code
(e.g. "lower is always better for a *_count metric") would violate the
explicit "never silently choose... when the question is ambiguous"
principle (Phase 2B spec section 0) the moment a future metric doesn't
fit that assumption (e.g. a fidelity or score-like metric where higher
is better -- which already exists in this exact schema as `score`).

**Current mitigation (implemented, not a gap fix):**
`qch.nl.service.derive_clarification_options` asks for BOTH the metric
AND the direction at once ("lowest structural toffoli count" /
"highest structural toffoli count" / ...) whenever the original
question used a bare superlative, so the system never guesses and the
clarification round-trip still reaches a valid, unambiguous follow-up
question. See `qch/nl/service.py`'s `_build_clarified_question` /
`derive_clarification_options` docstrings for the full reasoning.

**Possible future improvement:** an explicit, schema-declared
`improvement_direction: "lower_is_better" | "higher_is_better"` per
metric in `docs/qch_query_schema.json`, letting a bare "best" resolve
in ONE clarification turn (metric only) instead of two axes at once.
Not implemented here -- a schema change like this should be reviewed on
its own, not slipped in as a side effect of a clarification UX
improvement.

**Estimated complexity:** low (a schema field + one lookup), but
requires a deliberate, reviewed decision about every metric's
direction, which is out of scope for this milestone.

**Update (Phase 2C):** the same "ask for both axes at once" mitigation
is now ALSO enforced independently by `SemanticGuard`
(`src/qch/nl/semantic_guard.py`'s `SUPERLATIVE_AMBIGUITY` rule), so a
bare superlative is caught even if a future backend's planner (or the
Phase 2B service's own planning-time clarification path) somehow
missed it -- verified live against the real Qwen2.5-14B model in the
Phase 2C acceptance rerun (question J). This is defense in depth, not
a fix for the underlying gap: the schema still has no
`improvement_direction` field, and Phase 2C does not add one.

**User value:** medium -- reduces a two-turn clarification to one turn
for the single most common superlative phrasing.

## 6. `traverse` predecessor/successor direction vs. "branched from" phrasing -- RESOLVED (Phase 2C.1)

**Status: fixed and verified in Phase 2C.1** (see
`docs/QCH_NL_SEMANTIC_RELIABILITY_PHASE2C.md` section on the 2C.1
patch). `SemanticGuard.expected_branch_traversal_direction()` now
distinguishes the subject role ("X branch(ed) from", "X was branched
from" -> `predecessors`) from the object-of-"from" role ("branch(ed)
from X" -> `successors`) for a NAMED version in a `branched_from`
`traverse` step, and safely flips the ONE `direction` field when they
disagree. Verified on the real Qwen2.5-14B model against the real
database: the original failing question now returns 22 real branches
(previously an empty, wrong result) via a recorded `SAFE_REPAIR`.
**Scope note:** the fix covers "branch(ed)" wording specifically (per
the spec's own test-phrasing examples); "fork(ed) from" phrasing is
NOT covered by this new rule (it remains covered only by the
pre-existing, separate relation-TYPE-selection rule, which does
recognize "forked from" for choosing `branched_from` over another
relation type -- just not for choosing traversal DIRECTION). This
narrower scope was a deliberate choice, not an oversight -- expanding
it was out of scope for the 2C.1 patch.

Original finding, preserved for history:

**Discovered during:** Phase 2C's unseen-question generalization run
(`results/phase2c/unseen_questions_run_2026-09-22.json`, category
`relation_semantics_branch`).

**Example question:** "Which validated-unpromoted versions branched
from `ecdsafail:1196b9f`?"

**What went wrong:** the real Qwen2.5-14B planner produced
`traverse(version=ecdsafail:1196b9f, direction=predecessors,
relation_type=branched_from)`. "Branched FROM X" asks for X's
CHILDREN (the branches that came off of X) -- `direction=successors`.
`predecessors` asks the opposite question ("what did X branch from").
The plan was schema-valid and `SemanticGuard`'s own `ACCEPT` outcome
was itself a FALSE NEGATIVE: it executed to an honestly-empty result
(no incoming `branched_from` edges thing) rather than the real 3
branches `ecdsafail:1196b9f` actually has (independently verified by
direct SQL).

**Why current SemanticGuard rules don't catch it:** `SemanticGuard`'s
direction-consistency rules (see this doc's Phase 2C update, and
`docs/QCH_NL_SEMANTIC_RELIABILITY_PHASE2C.md` section 6) only cover
ranking/delta sort direction and `relation_type` choice -- nothing
today checks a `traverse` operator's own `predecessors`/`successors`/
`ancestors`/`descendants` direction against "branched from"/"branch
of"/"came from" phrasing.

**Possible future improvement:** a `TRAVERSAL_DIRECTION_CONTRADICTION`
rule: detect "X branched/forked FROM Y" (Y is the source, X the
target) vs. "Y is the [promoted] ancestor/parent of X" phrasing, and
require `direction=successors` when the question's grammatical SOURCE
of the branch matches the plan's `version` param (asking for its
children), `predecessors` when it's the object of "branched from".

**Estimated complexity:** low-medium (a few more phrase patterns plus
one more `_inspect_plan`-style check on `traverse` steps), but real
English "X branched from Y" vs. "Y, from which X branched" phrasing
variety makes 100% precision harder than the delta-direction case.

**User value:** medium-high -- provenance/graph-traversal questions
are a named product-direction example (spec section 30: "Where did
this version branch from?").

## 5. Clarification options assume every ambiguity is metric-shaped

**Discovered during:** the Phase 2B real acceptance run, question G
("What happened to structural Toffoli count on August 21?").

**Example questions:** any date-without-year question that reaches
AMBIGUOUS ("...on August 21?", "...in early September?").

**Why current code can't express it:** `qch.nl.service.derive_clarification_options`
only knows how to build METRIC-shaped options (bare names, or
direction-qualified "lowest/highest <metric>" pairs). A temporal
ambiguity (missing year) has nothing to do with metrics at all, but
the function still falls through to its "no metric mentioned in the
text" branch and returns the FULL list of every resolvable metric as
if the user needed to pick one -- genuinely unhelpful and mildly
confusing for a question that only needed a year.

**Possible future improvement:** detect the ambiguity's SHAPE (metric
vs. temporal vs. something else) from the planning `Diagnostic.code`
(e.g. a backend could emit a distinct code like
`ambiguous_temporal_reference` instead of the generic
`backend_ambiguous` every ambiguity uses today) and branch
`derive_clarification_options` accordingly -- e.g. offering nothing
(just relay the clarification question text) or a short list of
plausible years drawn from the data's own `historical_time` range.

**Estimated complexity:** low-medium (mostly wiring a more specific
diagnostic code through; the "plausible years from real data" version
needs one more real-data query).

**User value:** low-medium -- a clearer clarification prompt for a
narrow but real question shape (temporal ambiguity), not a new
capability.

**Update (Phase 2C):** `SemanticGuard`'s OWN `TEMPORAL_AMBIGUITY` rule
(`src/qch/nl/semantic_guard.py`) does NOT have this bug -- it returns a
plain clarifying question with an empty options list, never a
misleading metric list. However, Phase 2B's `derive_clarification_options`
in `qch/nl/service.py` is unchanged and still has the original defect
described above when a backend's OWN planning-time ambiguity path is
what catches the temporal case (as it was for question G in the real
acceptance rerun, where the LLM itself returned an AMBIGUOUS status
before ever producing a plan for `SemanticGuard` to see). This gap is
still open for that path.
