"""Centralized configuration for locating the ECDSA.Fail dataset.

The ECDSA.Fail dataset (five historical versions of the secp256k1 point
-addition circuit) lives OUTSIDE this repository, in a frozen, read-only
dataset root that this project must never modify:

    <dataset root>/manifest.json

This module is the one place that knows the default local path and the
environment variable used to override it, so the rest of the app never
hard-codes an absolute filesystem path.

For portability (e.g. a Streamlit Community Cloud deployment that has no
access to a local C:\\ drive), src/ecdsa_adapter.py falls back to a small
bundled snapshot of the manifest committed inside this repo -- see
data/ecdsa/manifest_snapshot.json and scripts/snapshot_ecdsa_manifest.py.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Overridable via the environment; falls back to the developer's local
# dataset checkout. This default is only ever used when the environment
# variable is unset -- it is never assumed to exist.
ENV_VAR = "QCH_ECDSA_DATASET_ROOT"
DEFAULT_EXTERNAL_ROOT = Path(
    r"C:\Users\jilu\Project\QCDB\Data\ECDSA\qch-ecdsafail-dataset"
)

# Bundled, lightweight, derived copy of manifest.json for environments
# without access to the external dataset root. See
# scripts/snapshot_ecdsa_manifest.py for how it is (re)generated.
SNAPSHOT_PATH = PROJECT_ROOT / "data" / "ecdsa" / "manifest_snapshot.json"


def external_dataset_root() -> Path:
    """The configured (or default) external dataset root directory.

    This is a candidate path only -- callers must check for the presence
    of manifest.json themselves, since the directory may not exist or be
    reachable (e.g. on a cloud deployment).
    """
    override = os.environ.get(ENV_VAR)
    return Path(override) if override else DEFAULT_EXTERNAL_ROOT


def external_manifest_path() -> Path:
    return external_dataset_root() / "manifest.json"
