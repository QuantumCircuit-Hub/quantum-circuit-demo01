"""QCH Phase 2A-2: read-only inspection of the LOCAL machine's hardware
and already-installed local-inference tooling -- used only to write
the "candidate models and resource requirements" report the spec
requires BEFORE any model download. This module never downloads,
installs, or starts anything; every check here is passive (platform
info, `shutil.which`, an optional `nvidia-smi` query, an optional
`pip list`)."""

from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Any

_KNOWN_RUNTIME_EXECUTABLES = ("ollama", "llama-server", "llama-cpp", "lms")
_KNOWN_RUNTIME_PY_PACKAGES = ("vllm", "llama-cpp-python", "transformers", "ctransformers", "exllamav2")


def _try_psutil_memory_gb() -> float | None:
    try:
        import psutil  # optional dependency
    except ImportError:
        return None
    return round(psutil.virtual_memory().total / (1024**3), 1)


def _try_nvidia_smi() -> list[dict[str, str]] | None:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=True,
        )
    except Exception:
        return []
    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            gpus.append({"name": parts[0], "memory_total": parts[1], "driver_version": parts[2]})
    return gpus


def _installed_runtime_executables() -> list[str]:
    return [name for name in _KNOWN_RUNTIME_EXECUTABLES if shutil.which(name) is not None]


def _installed_runtime_py_packages() -> list[str]:
    found = []
    for pkg in _KNOWN_RUNTIME_PY_PACKAGES:
        try:
            __import__(pkg.replace("-", "_"))
            found.append(pkg)
        except ImportError:
            continue
    return found


def collect_environment_report() -> dict[str, Any]:
    gpus = _try_nvidia_smi()
    return {
        "os": platform.platform(),
        "python_version": platform.python_version(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_logical_cores": _cpu_count(),
        "ram_gb": _try_psutil_memory_gb(),
        "gpu_detected_via_nvidia_smi": gpus if gpus is not None else "nvidia-smi not found on PATH -- no NVIDIA GPU detected via this check, or driver not installed",
        "installed_local_inference_runtime_executables": _installed_runtime_executables(),
        "installed_local_inference_python_packages": _installed_runtime_py_packages(),
        "note": "This is a passive inspection only -- nothing is downloaded or started by this module.",
    }


def _cpu_count() -> int | None:
    import os

    return os.cpu_count()
