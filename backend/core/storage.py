from __future__ import annotations

import os
from pathlib import Path


def persistent_data_root(project_root: Path | None = None) -> Path:
    """Return the writable application-data root.

    Immutable Ubuntu releases must never write below their own checkout.  The
    explicit environment variable is preferred; the managed server layout is
    detected next; local development keeps using ``<project>/data``.
    """

    configured = (os.getenv("GD_PERSISTENT_DATA_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser()

    shared = Path("/opt/greendiamond/shared/persistent-data/data")
    root = project_root or Path(__file__).resolve().parents[2]
    if str(root).startswith("/opt/greendiamond/releases/") and shared.is_dir():
        return shared
    return root / "data"
