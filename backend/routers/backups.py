from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from backend.routers.auth import get_current_user

router = APIRouter(prefix="/backups", tags=["backups"])

# NOTA: Backups grandes + hosting con cuota por usuario = riesgo de dejar el sistema abajo
# si acumulamos .tar.gz. Por eso soportamos subir a Google Drive y podar local.


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").upper()


def _require_admin(user: dict) -> None:
    if _role(user) not in ("ADMIN", "SUPERADMIN"):
        raise HTTPException(403, "Solo Admin")


BASE_DIR = Path(__file__).resolve().parents[2]  # /home/.../crm
HOME_DIR = BASE_DIR.parent                  # /home/.../bf68ec5
PUBLIC_HTML = HOME_DIR / "public_html"
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR = BASE_DIR / "runtime"
TMP_DIR = Path(
    os.getenv("CRM_BACKUP_TMP_DIR")
    or (RUNTIME_DIR / "backup-tmp" if RUNTIME_DIR.is_dir() else BASE_DIR / ".backup_tmp")
)
TMP_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR = BACKUP_DIR / "_jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
CFG_PATH = BASE_DIR / "data" / "backup_config.json"


def _load_cfg() -> dict:
    """
    Configurable sin tocar código:
    - crm/data/backup_config.json (prod)
    """
    try:
        if CFG_PATH.exists():
            return json.loads(CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_drive_cfg() -> dict:
    cfg = _load_cfg() or {}
    d = cfg.get("drive") or cfg.get("google_drive") or {}
    if not isinstance(d, dict):
        d = {}
    # defaults
    d.setdefault("enabled", False)
    d.setdefault("folder_id", "")
    # Mantener un solo archivo local para no volver a llenar cuota.
    d.setdefault("keep_local", 1)
    return d


_DRIVE_SVC = None
_DRIVE_SVCS: dict[str, Any] = {}


def _drive_service_for(sa_path: Path):
    """
    Construye (y cachea) un cliente Drive para un service account.
    """
    key = str(sa_path)
    if key in _DRIVE_SVCS:
        return _DRIVE_SVCS[key]
    # Imports lazy: si la lib no está, backups siguen funcionando local.
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if not sa_path.exists():
        raise RuntimeError(f"No existe service account para Drive: {sa_path}")

    creds = service_account.Credentials.from_service_account_file(
        str(sa_path),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    _DRIVE_SVCS[key] = svc
    return svc


def _drive_sa_candidates() -> List[Path]:
    """
    Permitimos 2+ service accounts para evitar bloqueos por permisos.
    Orden:
    - drive.service_account_jsons (si existe) o drive.service_account_json (legacy)
    - crm/data/drive_backup.json (si existe)
    - crm/data/drive.json (por compat; OJO: este puede ser el SA de Drive Assets)
    """
    dcfg = _get_drive_cfg()
    out: List[Path] = []
    raw_list = dcfg.get("service_account_jsons")
    if isinstance(raw_list, list):
        for x in raw_list:
            try:
                p = Path(str(x))
                if not p.is_absolute():
                    p = (BASE_DIR / p).resolve()
                out.append(p)
            except Exception:
                pass
    else:
        sa_override = str(dcfg.get("service_account_json") or "").strip()
        if sa_override:
            p = Path(sa_override)
            if not p.is_absolute():
                p = (BASE_DIR / p).resolve()
            out.append(p)

    p2 = BASE_DIR / "data" / "drive_backup.json"
    p3 = BASE_DIR / "data" / "drive.json"
    if p2 not in out:
        out.append(p2)
    if p3 not in out:
        out.append(p3)

    # de-dupe conservando orden
    seen = set()
    final: List[Path] = []
    for p in out:
        sp = str(p)
        if sp in seen:
            continue
        seen.add(sp)
        final.append(p)
    return final


def _drive_service_for_folder(folder_id: str):
    """
    Elige el primer service account que tenga acceso a la carpeta de Drive.
    """
    last_err: str = ""
    for sa in _drive_sa_candidates():
        if not sa.exists():
            continue
        try:
            svc = _drive_service_for(sa)
            # Chequeo liviano de permisos (404/403 si no tiene acceso)
            svc.files().get(fileId=folder_id, fields="id", supportsAllDrives=True).execute()
            return svc, sa
        except Exception as e:
            last_err = str(e)
            continue
    raise RuntimeError(f"No hay service account con acceso al folder_id={folder_id}. last_err={last_err[:220]}")


def _drive_upload(archive: Path, backup_id: str, label: str, job_id: str) -> dict:
    """
    Sube el .tar.gz a Drive (resumable).
    Requiere que la carpeta destino esté compartida con el service account.
    """
    dcfg = _get_drive_cfg()
    folder_id = str(dcfg.get("folder_id") or "").strip()
    if not folder_id:
        raise RuntimeError("Drive enabled pero falta drive.folder_id en crm/data/backup_config.json")

    from googleapiclient.http import MediaFileUpload

    svc, sa_path = _drive_service_for_folder(folder_id)
    # Nombre visible en Drive
    safe_label = label.strip()[:60]
    name = f"CRM_BACKUP_{backup_id}{(' - ' + safe_label) if safe_label else ''}.tar.gz"

    media = MediaFileUpload(str(archive), mimetype="application/gzip", resumable=True, chunksize=10 * 1024 * 1024)
    req = svc.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media,
        fields="id,name,webViewLink,webContentLink,size,createdTime",
        supportsAllDrives=True,
    )

    resp = None
    last_pct = -1
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            # Mapear al rango 80..96 para que se note el progreso.
            ui_pct = 80 + int(min(1.0, status.progress()) * 16)
            if pct != last_pct:
                _write_job(job_id, {"backup_id": backup_id, "status": "running", "stage": "UPLOAD_DRIVE", "percent": ui_pct})
                last_pct = pct

    out = resp or {}
    try:
        out["_sa_path"] = str(sa_path)
    except Exception:
        pass
    return out


def _prune_local_archives(keep: int = 1) -> None:
    """
    Mantiene N .tar.gz más nuevos y elimina el resto (para evitar cuota).
    Deja los .json (metadatos) para historial.
    """
    try:
        keep = int(keep)
    except Exception:
        keep = 1
    keep = max(0, keep)
    arcs = sorted(BACKUP_DIR.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for ap in arcs[keep:]:
        try:
            ap.unlink(missing_ok=True)
        except Exception:
            pass


@dataclass
class Dsn:
    host: str
    port: int
    user: str
    password: str
    dbname: str


def _parse_dsn() -> Dsn:
    dsn = os.environ.get("DATABASE_URL") or ""
    if not dsn:
        raise RuntimeError("DATABASE_URL no está configurada")
    u = urlparse(dsn)
    # postgresql+psycopg2://user:pass@host:port/db
    user = u.username or ""
    pw = u.password or ""
    host = u.hostname or "127.0.0.1"
    port = int(u.port or 5432)
    db = (u.path or "").lstrip("/") or ""
    if not user or not pw or not db:
        raise RuntimeError("DATABASE_URL incompleta (user/pass/db)")
    return Dsn(host=host, port=port, user=user, password=pw, dbname=db)


def _run(cmd: List[str], env: Optional[Dict[str, str]] = None) -> None:
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
    )
    if p.returncode != 0:
        raise RuntimeError((p.stdout or "").strip() or f"Command failed: {' '.join(cmd)}")


def _backup_id_now() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _meta_path(backup_id: str) -> Path:
    return BACKUP_DIR / f"{backup_id}.json"


def _archive_path(backup_id: str) -> Path:
    return BACKUP_DIR / f"{backup_id}.tar.gz"

def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"

def _write_job(job_id: str, data: Dict[str, Any]) -> None:
    data = dict(data or {})
    data.setdefault("job_id", job_id)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    p = _job_path(job_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

def _read_job(job_id: str) -> Dict[str, Any]:
    p = _job_path(job_id)
    if not p.exists():
        raise HTTPException(404, "Job no encontrado")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"job_id": job_id, "status": "error", "error": "No pude leer el estado del job"}

def _latest_archive_size() -> int:
    best = 0
    for ap in BACKUP_DIR.glob("*.tar.gz"):
        try:
            best = max(best, int(ap.stat().st_size))
        except Exception:
            pass
    return best


def _read_meta(backup_id: str) -> dict:
    mp = _meta_path(backup_id)
    if not mp.exists():
        return {}
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _drive_download(file_id: str, dest: Path) -> None:
    """
    Descarga un archivo de Drive usando service account.
    """
    # Intentamos con cualquier service account que funcione (el archivo puede estar
    # en una carpeta compartida distinta a la de backups).
    last_err: str = ""
    svc = None
    for sa in _drive_sa_candidates():
        if not sa.exists():
            continue
        try:
            svc = _drive_service_for(sa)
            break
        except Exception as e:
            last_err = str(e)
            continue
    if svc is None:
        raise RuntimeError(f"No pude inicializar Drive service. last_err={last_err[:220]}")
    from googleapiclient.http import MediaIoBaseDownload
    import io

    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    fh = io.FileIO(str(dest), "wb")
    dl = MediaIoBaseDownload(fh, req, chunksize=10 * 1024 * 1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
    try:
        fh.close()
    except Exception:
        pass

def _run_popen(cmd: List[str], env: Optional[Dict[str, str]] = None) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
    )

def _job_worker(job_id: str, backup_id: str, label: str, created_by: str) -> None:
    started_at = datetime.now().isoformat(timespec="seconds")
    try:
        _write_job(
            job_id,
            {
                "backup_id": backup_id,
                "status": "running",
                "stage": "PREP",
                "percent": 0,
                "label": label,
                "created_by": created_by,
                "started_at": started_at,
            },
        )

        dsn = _parse_dsn()
        dump_path = TMP_DIR / f"{backup_id}.dump"

        # 1) DB dump
        _write_job(job_id, {"backup_id": backup_id, "status": "running", "stage": "DB_DUMP", "percent": 3, "label": label, "created_by": created_by, "started_at": started_at})
        env = os.environ.copy()
        env["PGPASSWORD"] = dsn.password
        p = _run_popen(
            [
                "pg_dump",
                "-h",
                dsn.host,
                "-p",
                str(dsn.port),
                "-U",
                dsn.user,
                "-F",
                "c",
                "-f",
                str(dump_path),
                dsn.dbname,
            ],
            env=env,
        )
        while p.poll() is None:
            # indeterminado, pero movemos un poquito para que se sienta vivo
            _write_job(job_id, {"backup_id": backup_id, "status": "running", "stage": "DB_DUMP", "percent": 6, "label": label, "created_by": created_by, "started_at": started_at})
            time.sleep(1.5)
        out = (p.stdout.read() if p.stdout else "") if p.returncode else ""
        if p.returncode != 0:
            raise RuntimeError((out or "").strip() or "pg_dump falló")

        # 2) Archive
        archive = _archive_path(backup_id)
        includes: List[str] = [str(BASE_DIR.name)]
        if PUBLIC_HTML.exists():
            includes.append(str(PUBLIC_HTML.name))

        expected_bytes = _latest_archive_size()
        # baseline si no hay historial
        expected_bytes = max(expected_bytes, 500_000_000)
        _write_job(
            job_id,
            {
                "backup_id": backup_id,
                "status": "running",
                "stage": "ARCHIVE",
                "percent": 10,
                "expected_bytes": expected_bytes,
                "label": label,
                "created_by": created_by,
                "started_at": started_at,
            },
        )

        cmd = [
            "tar",
            "-czf",
            str(archive),
            "--exclude",
            f"{BASE_DIR.name}/backups",
            "--exclude",
            f"{BASE_DIR.name}/node_modules",
            "--exclude",
            f"{BASE_DIR.name}/.venv",
            "--exclude",
            f"{BASE_DIR.name}/crm_app",
            "-C",
            str(HOME_DIR),
            *includes,
        ]
        p2 = _run_popen(cmd, env=None)
        while p2.poll() is None:
            try:
                sz = int(archive.stat().st_size) if archive.exists() else 0
            except Exception:
                sz = 0
            frac = min(1.0, (sz / float(expected_bytes)) if expected_bytes else 0.0)
            pct = 10 + int(frac * 85)
            pct = max(10, min(95, pct))
            _write_job(
                job_id,
                {
                    "backup_id": backup_id,
                    "status": "running",
                    "stage": "ARCHIVE",
                    "percent": pct,
                    "current_bytes": sz,
                    "expected_bytes": expected_bytes,
                    "label": label,
                    "created_by": created_by,
                    "started_at": started_at,
                },
            )
            time.sleep(2.0)
        out2 = (p2.stdout.read() if p2.stdout else "") if p2.returncode else ""
        if p2.returncode != 0:
            raise RuntimeError((out2 or "").strip() or "tar falló")

        # 3) Meta + cleanup
        _write_job(job_id, {"backup_id": backup_id, "status": "running", "stage": "META", "percent": 97, "label": label, "created_by": created_by, "started_at": started_at})
        meta = {
            "id": backup_id,
            "label": label,
            "type": "MANUAL",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "created_by": created_by,
            "db": {"host": dsn.host, "port": dsn.port, "user": dsn.user, "dbname": dsn.dbname},
            "includes": includes,
            "dump_file": dump_path.name,
            "dump_relpath": str(dump_path.relative_to(BASE_DIR)),
            "archive": archive.name,
            "kind": "full",
            "ok": True,
        }
        _meta_path(backup_id).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # 4) Optional: subir a Drive para no romper la cuota del hosting
        dcfg = _get_drive_cfg()
        drive_info = None
        if bool(dcfg.get("enabled")):
            _write_job(job_id, {"backup_id": backup_id, "status": "running", "stage": "UPLOAD_DRIVE", "percent": 80, "label": label, "created_by": created_by, "started_at": started_at})
            drive_info = _drive_upload(archive, backup_id, label, job_id)
            # persistimos en meta
            try:
                meta["drive"] = {
                    "file_id": drive_info.get("id"),
                    "name": drive_info.get("name"),
                    "webViewLink": drive_info.get("webViewLink"),
                    "webContentLink": drive_info.get("webContentLink"),
                    "size": drive_info.get("size"),
                    "createdTime": drive_info.get("createdTime"),
                    "folder_id": str(dcfg.get("folder_id") or ""),
                    "sa_path": str(drive_info.get("_sa_path") or ""),
                }
                _meta_path(backup_id).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

            # Podamos local (dejamos 1 por defecto). Esto es CLAVE para no caer de nuevo.
            _write_job(job_id, {"backup_id": backup_id, "status": "running", "stage": "PRUNE_LOCAL", "percent": 98})
            _prune_local_archives(int(dcfg.get("keep_local") or 1))

        try:
            dump_path.unlink(missing_ok=True)
        except Exception:
            pass

        _write_job(job_id, {"backup_id": backup_id, "status": "done", "stage": "DONE", "percent": 100, "label": label, "created_by": created_by, "started_at": started_at})
    except Exception as e:
        _write_job(
            job_id,
            {
                "backup_id": backup_id,
                "status": "error",
                "stage": "ERROR",
                "percent": 100,
                "label": label,
                "created_by": created_by,
                "started_at": started_at,
                "error": str(e),
            },
        )


@router.get("")
def list_backups(user: dict = Depends(get_current_user)):
    _require_admin(user)
    items: List[Dict[str, Any]] = []
    for mp in sorted(BACKUP_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            data = {"id": mp.stem, "ok": False}
        aid = data.get("id") or mp.stem
        ap = _archive_path(aid)
        data["has_archive"] = ap.exists()
        if ap.exists():
            try:
                data["size_bytes"] = ap.stat().st_size
            except Exception:
                pass
        items.append(data)
    return {"ok": True, "items": items}

@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    return {"ok": True, "job": _read_job(job_id)}


@router.post("/run_async")
def run_backup_async(payload: dict | None = None, user: dict = Depends(get_current_user)):
    """
    Lanza un backup en background y devuelve un job_id.
    Esto permite salir de la vista y seguir trabajando.
    """
    _require_admin(user)

    # Si hay un job corriendo, no lanzamos otro (evita llenar disco/cpu por accidente).
    for jp in JOBS_DIR.glob("*.json"):
        try:
            j = json.loads(jp.read_text(encoding="utf-8"))
            if j.get("status") == "running":
                return {"ok": True, "job_id": j.get("job_id") or jp.stem, "already_running": True}
        except Exception:
            continue

    payload = payload or {}
    label = str(payload.get("label") or payload.get("descripcion") or payload.get("description") or "").strip()
    if len(label) > 140:
        label = label[:140].strip()

    backup_id = _backup_id_now()
    job_id = backup_id
    created_by = user.get("username") or user.get("email") or user.get("name") or "admin"

    _write_job(
        job_id,
        {
            "backup_id": backup_id,
            "job_id": job_id,
            "status": "running",
            "stage": "QUEUED",
            "percent": 0,
            "label": label,
            "created_by": created_by,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        },
    )

    t = threading.Thread(target=_job_worker, args=(job_id, backup_id, label, created_by), daemon=True)
    t.start()
    return {"ok": True, "job_id": job_id, "backup_id": backup_id}


@router.post("/run")
def run_backup(payload: dict | None = None, user: dict = Depends(get_current_user)):
    _require_admin(user)
    payload = payload or {}
    label = str(payload.get("label") or payload.get("descripcion") or payload.get("description") or "").strip()
    if len(label) > 140:
        label = label[:140].strip()
    backup_id = _backup_id_now()

    dsn = _parse_dsn()
    # IMPORTANTE:
    # No podemos guardar el dump dentro de crm/backups porque el tar excluye esa carpeta
    # (y tar aplica --exclude incluso a paths pasados explícitamente).
    # Entonces generamos el dump en crm/.backup_tmp para garantizar que SIEMPRE quede dentro del .tar.gz.
    dump_path = TMP_DIR / f"{backup_id}.dump"

    # 1) DB dump (custom format, restoreable)
    env = os.environ.copy()
    env["PGPASSWORD"] = dsn.password
    _run(
        [
            "pg_dump",
            "-h",
            dsn.host,
            "-p",
            str(dsn.port),
            "-U",
            dsn.user,
            "-F",
            "c",
            "-f",
            str(dump_path),
            dsn.dbname,
        ],
        env=env,
    )

    # 2) Archivar "todo el sistema" (CRM + public_html + dump)
    archive = _archive_path(backup_id)
    includes: List[str] = []
    includes.append(str(BASE_DIR.name))  # crm
    if PUBLIC_HTML.exists():
        includes.append(str(PUBLIC_HTML.name))  # public_html

    # tar desde HOME_DIR para incluir crm y public_html con rutas relativas limpias
    cmd = [
        "tar",
        "-czf",
        str(archive),
        "--exclude",
        f"{BASE_DIR.name}/backups",
        "--exclude",
        f"{BASE_DIR.name}/node_modules",
        "--exclude",
        f"{BASE_DIR.name}/.venv",
        "--exclude",
        f"{BASE_DIR.name}/crm_app",
    ]
    cmd += ["-C", str(HOME_DIR)]
    cmd += includes

    _run(cmd)

    # 3) Meta + cleanup
    meta = {
        "id": backup_id,
        "label": label,
        "type": "MANUAL",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": user.get("username") or user.get("email") or user.get("name") or "admin",
        "db": {"host": dsn.host, "port": dsn.port, "user": dsn.user, "dbname": dsn.dbname},
        "includes": includes,
        "dump_file": dump_path.name,
        "dump_relpath": str(dump_path.relative_to(BASE_DIR)),
        "archive": archive.name,
        "kind": "full",
        "ok": True,
    }
    _meta_path(backup_id).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # Limpieza dump (dejamos meta + tar)
    try:
        dump_path.unlink(missing_ok=True)  # py>=3.8 ok
    except Exception:
        pass

    return {"ok": True, "id": backup_id, "archive": f"/backups/{backup_id}/download"}


@router.get("/{backup_id}/download")
def download_backup(backup_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    ap = _archive_path(backup_id)
    if not ap.exists():
        raise HTTPException(404, "Backup no encontrado")
    return FileResponse(str(ap), media_type="application/gzip", filename=ap.name)


@router.delete("/{backup_id}")
def delete_backup(backup_id: str, user: dict = Depends(get_current_user)):
    _require_admin(user)
    ap = _archive_path(backup_id)
    mp = _meta_path(backup_id)
    if not ap.exists() and not mp.exists():
        raise HTTPException(404, "Backup no existe")
    try:
        if ap.exists():
            ap.unlink()
    except Exception:
        pass
    try:
        if mp.exists():
            mp.unlink()
    except Exception:
        pass
    return {"ok": True}


@router.post("/{backup_id}/restore")
def restore_backup(backup_id: str, payload: dict | None = None, user: dict = Depends(get_current_user)):
    """
    Restauración (peligrosa): reemplaza archivos y restaura BD.
    Requiere confirmación explícita.
    """
    _require_admin(user)
    payload = payload or {}
    confirm = str(payload.get("confirm") or "")
    if confirm != f"RESTORE {backup_id}":
        raise HTTPException(400, "Confirmación inválida. Debes enviar confirm='RESTORE <id>'")
    ap = _archive_path(backup_id)
    # Si no está local, intentamos bajarlo desde Drive (si el meta lo trae).
    temp_downloaded = False
    if not ap.exists():
        meta = _read_meta(backup_id)
        drive = meta.get("drive") if isinstance(meta, dict) else None
        file_id = (drive or {}).get("file_id") if isinstance(drive, dict) else None
        if file_id:
            try:
                _drive_download(str(file_id), ap)
                temp_downloaded = True
            except Exception as e:
                raise HTTPException(500, f"No pude descargar el backup desde Drive: {e}")
        else:
            raise HTTPException(404, "Backup no encontrado (ni local ni en Drive)")

    dsn = _parse_dsn()
    env = os.environ.copy()
    env["PGPASSWORD"] = dsn.password

    # 1) extrae tar en HOME_DIR (sobreescribe)
    _run(["tar", "-xzf", str(ap), "-C", str(HOME_DIR)], env=None)

    # 2) restaura DB desde el dump que quedó dentro de crm/.backup_tmp
    dump_path = (BASE_DIR / ".backup_tmp" / f"{backup_id}.dump")
    if not dump_path.exists():
        # fallback: por si el dump se movió en una versión anterior
        candidates = [
            BASE_DIR / f"{backup_id}.dump",
            BACKUP_DIR / f"{backup_id}.dump",
        ]
        dump_path = next((p for p in candidates if p.exists()), dump_path)
    if not dump_path.exists():
        raise HTTPException(500, "No encontré el dump de BD dentro del backup extraído.")

    # OJO: esto sobrescribe la BD completa.
    _run(
        [
            "pg_restore",
            "-h",
            dsn.host,
            "-p",
            str(dsn.port),
            "-U",
            dsn.user,
            "-d",
            dsn.dbname,
            "--clean",
            "--if-exists",
            str(dump_path),
        ],
        env=env,
    )

    # Limpieza: no queremos dejar dumps en el filesystem de producción
    try:
        dump_path.unlink(missing_ok=True)
    except Exception:
        pass
    # Si tuvimos que descargarlo “solo para restaurar”, lo borramos para no llenar cuota.
    if temp_downloaded:
        try:
            ap.unlink(missing_ok=True)
        except Exception:
            pass
        dcfg = _get_drive_cfg()
        if bool(dcfg.get("enabled")):
            _prune_local_archives(int(dcfg.get("keep_local") or 1))
    return {"ok": True, "restored": backup_id}
