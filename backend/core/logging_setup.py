from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(base_dir: Path) -> Path:
    """
    Configura logging a archivo con rotación.

    Objetivos:
    - Tener SIEMPRE un .log útil para soporte (RID + stacktrace).
    - Evitar que los logs crezcan infinito (rotación).

    Retorna la carpeta donde quedaron los logs.
    """

    # Preferimos /crm/logs en prod. Si no existe o no se puede crear, caemos a /crm/data/debug.
    candidates = [base_dir / "logs", base_dir / "data" / "debug"]
    log_dir: Path | None = None
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            log_dir = d
            break
        except Exception:
            continue

    if log_dir is None:
        # Último recurso: logging a stderr solamente.
        logging.basicConfig(level=logging.INFO)
        return base_dir

    log_path = log_dir / "app.log"
    err_path = log_dir / "error.log"

    # Evitar duplicar handlers si Passenger recarga el módulo.
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "").endswith(str(log_path)) for h in root.handlers):
        return log_dir

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    h_all = RotatingFileHandler(str(log_path), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    h_all.setLevel(logging.INFO)
    h_all.setFormatter(fmt)

    h_err = RotatingFileHandler(str(err_path), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    h_err.setLevel(logging.ERROR)
    h_err.setFormatter(fmt)

    root.setLevel(logging.INFO)
    root.addHandler(h_all)
    root.addHandler(h_err)

    # Silenciar ruido típico
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    root.info("[logging] enabled file logs at %s", str(log_dir))
    return log_dir

