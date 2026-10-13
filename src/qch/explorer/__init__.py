"""Version Evolution Explorer: read-only search -> neighborhood -> details.

Pure logic lives here; the only SQL is in `qch.storage.sqlite.readonly`.
See docs/QCH_VERSION_EVOLUTION_EXPLORER.md.
"""

from qch.explorer.graph import (  # noqa: F401
    DEFAULT_MAX_NODES,
    GraphView,
    NeighborhoodState,
    clamp_max_nodes,
    expand,
    project,
    reset,
    start,
)
from qch.explorer.metrics import official_comparison, score_reduction_percent, structural_comparison  # noqa: F401
from qch.explorer.service import RELATION_CAVEAT, RELATION_DESCRIPTIONS, RELATION_LABELS, EvolutionExplorer, relation_label  # noqa: F401
from qch.explorer.timefmt import format_utc  # noqa: F401
