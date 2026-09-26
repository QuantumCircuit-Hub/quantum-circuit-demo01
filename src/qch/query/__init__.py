"""QCH Query Engine -- Phase 1 (deterministic, no LLM).

    from qch import QCH
    from qch.query import QCHQueryPlan, QCHQueryExecutor

    hub = QCH.open("my-qch.sqlite3")
    plan = QCHQueryPlan.from_dict({"operation": "get_metric", "version": "V1", "metric": "toffoli_count"})
    result = QCHQueryExecutor(hub).execute(plan)

See src/qch/query/models.py for QCHQueryPlan/QCHQueryResult/QCHQueryStatus,
src/qch/query/operators.py for the primitive and compound operators, and
docs/DB5_A110_QCH_QUERY_ENGINE_PHASE1.md for the design this package
implements.

This package is intentionally standalone within qch: it depends only on
the public qch.QCH service surface (hub.circuits/versions/metrics/
benchmarks/evolution), never on a storage backend directly, and
introduces no new persisted schema.
"""

from qch.query.models import QCHQueryPlan, QCHQueryResult, QCHQueryStatus, QCHQueryStep
from qch.query.operators import OPERATORS, QCHQueryPlanError
from qch.query.executor import QCHQueryExecutor

__all__ = [
    "QCHQueryPlan",
    "QCHQueryStep",
    "QCHQueryResult",
    "QCHQueryStatus",
    "QCHQueryExecutor",
    "QCHQueryPlanError",
    "OPERATORS",
]
