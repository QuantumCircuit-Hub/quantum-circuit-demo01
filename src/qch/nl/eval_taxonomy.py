"""QCH Phase 2A-2: the error taxonomy used to tag every LLM-evaluation
case result. A single case may carry several tags at once (e.g. a
response that is both WRONG_METRIC and WRONG_COMPARATOR) -- see
docs/QCH_NL_LLM_EVALUATION_PHASE2A2.md section 18/19 on why one
accuracy number must never hide this.
"""

from __future__ import annotations

from enum import Enum


class ErrorTag(str, Enum):
    MALFORMED_OUTPUT = "malformed_output"
    WRONG_STATUS = "wrong_status"
    UNKNOWN_OPERATOR_OUTPUT = "unknown_operator_output"
    INVENTED_OPERATOR = "invented_operator"
    WRONG_OPERATOR = "wrong_operator"
    INVENTED_METRIC = "invented_metric"
    WRONG_METRIC = "wrong_metric"
    INVENTED_RELATION = "invented_relation"
    WRONG_RELATION = "wrong_relation"
    WRONG_COMPARATOR = "wrong_comparator"
    WRONG_SORT_DIRECTION = "wrong_sort_direction"
    WRONG_LIMIT = "wrong_limit"
    WRONG_TEMPORAL_FILTER = "wrong_temporal_filter"
    INVENTED_ENTITY = "invented_entity"
    WRONG_VERSION_REFERENCE = "wrong_version_reference"
    MISSING_STEP = "missing_step"
    EXTRA_STEP = "extra_step"
    OVER_PLANNED_AMBIGUITY = "over_planned_ambiguity"
    FALSE_UNSUPPORTED = "false_unsupported"
    SCHEMA_ESCAPE_ATTEMPT = "schema_escape_attempt"
    OTHER_SEMANTIC_MISMATCH = "other_semantic_mismatch"
