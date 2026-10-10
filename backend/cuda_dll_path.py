"""Make pip-installed NVIDIA runtime DLLs (cuBLAS / cuDNN) findable on Windows.

faster-whisper's CTranslate2 needs cublas64_12.dll and cuDNN 9 at CUDA init.
In a torch-free environment (backend/requirements-lite.txt) they come from the
`nvidia-cublas-cu12` / `nvidia-cudnn-cu12` wheels, which unpack into
site-packages/nvidia/<lib>/bin — a directory Windows does not search by
default. With torch installed, torch/lib supplies them instead.

No-op when the directories don't exist (torch env, non-Windows, CPU-only), so
importing this is always safe. Call before importing faster_whisper.
"""

from __future__ import annotations

import glob
import os
import sys

_done = False


def add_nvidia_wheel_dll_dirs() -> list[str]:
    global _done
    added: list[str] = []
    if _done or sys.platform != "win32":
        return added
    _done = True
    roots = {os.path.join(p, "nvidia") for p in sys.path if p}
    for root in sorted(roots):
        for d in sorted(glob.glob(os.path.join(root, "*", "bin"))):
            if os.path.isdir(d) and any(n.lower().endswith(".dll") for n in os.listdir(d)):
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                added.append(d)
    return added
