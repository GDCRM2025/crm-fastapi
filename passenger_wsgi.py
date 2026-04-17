import os
import sys

# Passenger (cPanel) expects a WSGI callable named `application`.
# FastAPI is ASGI, so we wrap it via a small adapter (a2wsgi).
#
# IMPORTANT (prod incident): in this server there has been another "backend/" package
# under /home/bf68ec5 that shadowed the real app package (/home/bf68ec5/crm/backend),
# causing random 404s (e.g. /crm/login) depending on cwd/sys.path.
# We force cwd + sys.path to guarantee we import the correct application code.

APP_ROOT = os.path.realpath(os.path.dirname(__file__))  # /home/bf68ec5/crm
os.chdir(APP_ROOT)

bad_prefixes = (
    os.path.realpath(os.path.dirname(APP_ROOT)),  # /home/bf68ec5
    os.path.realpath(os.path.join(os.path.dirname(APP_ROOT), "public_html", "crm")),
)

cleaned = []
for p in list(sys.path):
    rp = os.path.realpath(p) if p else ""
    if not rp:
        continue
    if rp == APP_ROOT:
        continue
    if any(rp == bp or rp.startswith(bp + os.sep) for bp in bad_prefixes):
        continue
    cleaned.append(p)

sys.path[:] = [APP_ROOT] + cleaned

# Optional: print minimal boot info to Passenger log (helps debug mismatched envs).
try:  # pragma: no cover
    import backend as _backend  # noqa: F401

    print("PASSENGER_BOOT_OK python=", sys.executable)
    print("PASSENGER_BOOT_BACKEND_FILE=", getattr(_backend, "__file__", None))
except Exception:
    pass

from backend.main import app

try:
    from a2wsgi import ASGIMiddleware  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "Missing dependency: a2wsgi. Install it in the Passenger venv:\n"
        "  /home/bf68ec5/virtualenv/crm/3.10/bin/python3.10_bin -m pip install -U a2wsgi\n"
        f"Original error: {type(e).__name__}: {e}"
    )

application = ASGIMiddleware(app)
