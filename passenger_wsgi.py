import os
import sys

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(__file__))

# Passenger (cPanel) expects a WSGI callable named `application`.
# FastAPI is ASGI, so we wrap it via a small adapter.
from backend.main import app

try:
    from a2wsgi import ASGIMiddleware  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "Missing dependency: a2wsgi. Install it in the Passenger venv:\n"
        "  /home/bf68ec5/virtualenv/crm/3.10/bin/python -m pip install -U a2wsgi\n"
        f"Original error: {type(e).__name__}: {e}"
    )

application = ASGIMiddleware(app)

