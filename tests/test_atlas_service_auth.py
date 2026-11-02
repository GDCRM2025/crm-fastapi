import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.routers.atlas_service import require_atlas_service
from fastapi import HTTPException
import os

def test_atlas_token_fails_closed(monkeypatch):
    monkeypatch.delenv("ATLAS_FINANCE_SERVICE_TOKEN", raising=False)
    try:
        require_atlas_service("Bearer test")
        assert False
    except HTTPException as exc:
        assert exc.status_code == 503

def test_atlas_token_requires_exact_secret(monkeypatch):
    monkeypatch.setenv("ATLAS_FINANCE_SERVICE_TOKEN", "x" * 40)
    try:
        require_atlas_service("Bearer " + "y" * 40)
        assert False
    except HTTPException as exc:
        assert exc.status_code == 401
    require_atlas_service("Bearer " + "x" * 40)
