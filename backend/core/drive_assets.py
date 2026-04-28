from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.core.quote_assets import normalize_marca


_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
_MIME_FOLDER = "application/vnd.google-apps.folder"
_MIME_SHORTCUT = "application/vnd.google-apps.shortcut"
_A4_W = int(os.getenv("GD_QUOTE_ASSET_W", "1240"))  # ~A4 @150dpi
_A4_H = int(os.getenv("GD_QUOTE_ASSET_H", "1754"))
_BG_JPEG_QUALITY = int(os.getenv("GD_QUOTE_ASSET_JPEG_QUALITY", "82"))

def _log_once(tag: str, msg: str, every_seconds: int = 600) -> None:
    try:
        base = Path(__file__).resolve().parents[2] / "data" / "debug"
        base.mkdir(parents=True, exist_ok=True)
        fp = base / f"drive_assets_{tag}.log"
        now = time.time()
        if fp.exists():
            try:
                if (now - fp.stat().st_mtime) < float(every_seconds):
                    return
            except Exception:
                pass
        fp.write_text(f"{int(now)} {msg}\n", encoding="utf-8")
        print(f"[drive_assets] {msg}")
    except Exception:
        # no bloquear PDF si logging falla
        pass


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified_time: str  # RFC3339
    target_id: str = ""
    target_mime_type: str = ""


def _drive_service():
    """
    Drive API client (service account).

    Requisitos:
    - env `GD_DRIVE_SA_JSON` (o `GOOGLE_APPLICATION_CREDENTIALS`) apuntando a un JSON de Service Account
    - compartir las carpetas/archivos con el email de la Service Account.
    """
    json_path = (os.getenv("GD_DRIVE_SA_JSON") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if not json_path:
        # Default path dentro del repo (más fácil en hosting: solo subir el archivo).
        try:
            base = Path(__file__).resolve().parents[2]
            for name in ("drive_sa.json", "drive.json"):
                p = base / "data" / name
                if p.exists() and p.is_file():
                    json_path = str(p)
                    break
        except Exception:
            json_path = ""
    if not json_path:
        return None
    try:
        from google.oauth2.service_account import Credentials  # type: ignore
        from googleapiclient.discovery import build  # type: ignore

        creds = Credentials.from_service_account_file(json_path, scopes=_SCOPES)
        # cache_discovery=False evita escribir archivos temporales en algunos hostings.
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception:
        _log_once("service", f"No pude inicializar Drive API. Revisa JSON/Drive API habilitada. json_path={json_path}")
        return None


def drive_sa_info() -> dict:
    """
    Devuelve información NO sensible del Service Account configurado (si existe).
    """
    json_path = (os.getenv("GD_DRIVE_SA_JSON") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    chosen = ""
    if not json_path:
        try:
            base = Path(__file__).resolve().parents[2]
            for name in ("drive_sa.json", "drive.json"):
                p = base / "data" / name
                if p.exists() and p.is_file():
                    json_path = str(p)
                    chosen = name
                    break
        except Exception:
            json_path = ""
    if not json_path:
        return {"ok": False, "configured": False}
    info = {"ok": True, "configured": True, "json_path": json_path, "json_name": chosen or Path(json_path).name}
    try:
        import json
        obj = json.load(open(json_path, "r", encoding="utf-8"))
        ce = (obj.get("client_email") or "").strip()
        if ce:
            info["client_email"] = ce
    except Exception:
        pass
    return info


def download_file_by_id(
    file_id: str,
    *,
    dest_stem: str,
    refresh: bool = False,
    ttl_seconds: int = 600,
) -> str:
    """
    Descarga un archivo de Drive por file_id usando Service Account.

    Retorna una ruta `file://...` (as_uri) del archivo descargado cacheado.
    Si no hay service account disponible o falla, retorna "".
    """
    file_id = (file_id or "").strip()
    if not file_id:
        return ""

    svc = _drive_service()
    if not svc:
        return ""

    try:
        base = Path(__file__).resolve().parents[2] / "data" / "quote_assets" / "cache"
        base.mkdir(parents=True, exist_ok=True)
        stem = (dest_stem or "drive").strip()[:80]
        fp_raw = base / f"{stem}_{file_id}"

        def _is_fresh(p: Path) -> bool:
            try:
                if refresh:
                    return False
                return (time.time() - float(p.stat().st_mtime)) < float(ttl_seconds)
            except Exception:
                return False

        for ext in (".png", ".jpg", ".jpeg", ".webp", ".pdf"):
            p = fp_raw.with_suffix(ext)
            if p.exists() and p.is_file() and _is_fresh(p):
                return p.as_uri()

        # Descargar metadata para inferir extensión
        meta = (
            svc.files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,modifiedTime",
                supportsAllDrives=True,
            )
            .execute()
        )
        mime = (meta.get("mimeType") or "").lower()
        if "png" in mime:
            ext = ".png"
        elif "jpeg" in mime or "jpg" in mime:
            ext = ".jpg"
        elif "webp" in mime:
            ext = ".webp"
        elif "pdf" in mime:
            ext = ".pdf"
        else:
            # fallback: sin extensión (igualmente servible para base64)
            ext = ""

        out_path = fp_raw.with_suffix(ext) if ext else fp_raw

        # Descarga binaria
        from googleapiclient.http import MediaIoBaseDownload  # type: ignore
        import io

        req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        fh = io.BytesIO()
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _status, done = dl.next_chunk()

        content = fh.getvalue() or b""
        if not content:
            return ""
        try:
            if out_path.exists() and out_path.read_bytes() == content:
                # no tocar mtime si no cambió
                return out_path.as_uri()
        except Exception:
            pass
        out_path.write_bytes(content)
        return out_path.as_uri()
    except Exception as e:
        _log_once("download", f"Drive download falló. file_id={file_id} err={type(e).__name__}", every_seconds=120)
        return ""


def _rfc3339_to_ts(s: str) -> float:
    try:
        # Example: 2026-02-25T13:10:22.123Z
        # Python 3.10: fromisoformat no maneja 'Z' -> reemplazar.
        ss = (s or "").replace("Z", "+00:00")
        return datetime.fromisoformat(ss).timestamp()
    except Exception:
        return 0.0


def _list_folder(folder_id: str) -> List[DriveFile]:
    svc = _drive_service()
    if not svc:
        _log_once("list", f"Drive service no disponible. folder_id={folder_id}")
        return []
    if not folder_id:
        return []

    files: List[DriveFile] = []
    page_token: Optional[str] = None
    q = f"'{folder_id}' in parents and trashed=false"
    # shortcutDetails permite soportar assets guardados como "Shortcut" en Drive.
    fields = "nextPageToken, files(id,name,mimeType,modifiedTime,shortcutDetails(targetId,targetMimeType))"

    while True:
        try:
            resp = (
                svc.files()
                .list(
                    q=q,
                    fields=fields,
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
        except Exception as e:
            _log_once("list", f"Drive list falló. folder_id={folder_id} err={type(e).__name__}", every_seconds=120)
            return []
        for f in resp.get("files", []) or []:
            sd = f.get("shortcutDetails") or {}
            files.append(
                DriveFile(
                    id=str(f.get("id") or ""),
                    name=str(f.get("name") or ""),
                    mime_type=str(f.get("mimeType") or ""),
                    modified_time=str(f.get("modifiedTime") or ""),
                    target_id=str(sd.get("targetId") or ""),
                    target_mime_type=str(sd.get("targetMimeType") or ""),
                )
            )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return [f for f in files if f.id and f.name]

def _list_folder_recursive(folder_id: str, max_depth: int = 4) -> List[DriveFile]:
    """
    Lista recursivamente (hasta `max_depth`) siguiendo subcarpetas.
    Soporta shortcuts a carpetas (Drive permite "shortcut" de folder).

    Nota: limitamos profundidad para no explotar llamadas en hostings lentos.
    """
    if not folder_id:
        return []

    out: List[DriveFile] = []
    seen: set[str] = set()

    # BFS por niveles
    level: List[str] = [folder_id]
    depth = 0
    while level and depth < max_depth:
        next_level: List[str] = []
        for fid in level:
            if not fid or fid in seen:
                continue
            seen.add(fid)
            files = _list_folder(fid)
            out.extend(files)
            # Agregar subfolders + shortcuts a folder como próximos nodos
            for f in files:
                mt = (f.mime_type or "").lower()
                if mt == _MIME_FOLDER:
                    next_level.append(f.id)
                elif mt == _MIME_SHORTCUT and (f.target_mime_type or "").lower() == _MIME_FOLDER and f.target_id:
                    next_level.append(f.target_id)
        level = next_level
        depth += 1
    return out


def _pick_file(files: List[DriveFile], want: str) -> Optional[DriveFile]:
    """
    Elegir un archivo por tipo de asset usando heurísticas por nombre.
    """
    w = (want or "").lower()
    if not files:
        return None

    def ok_img(f: DriveFile) -> bool:
        mt = (f.mime_type or "").lower()
        if mt == _MIME_SHORTCUT and (f.target_mime_type or "").strip():
            mt = (f.target_mime_type or "").lower()
        if mt.startswith("image/"):
            return True
        # algunos Drive items pueden venir sin mime correcto; fallback por extensión
        n = (f.name or "").lower()
        return n.endswith((".png", ".jpg", ".jpeg", ".webp"))

    cand = [f for f in files if ok_img(f)]
    if not cand:
        return None

    # keywords por asset
    kw: List[str] = []
    if w == "portada":
        kw = ["portada", "cover"]
    elif w == "cotizacion":
        # Evitar keyword demasiado genérica ("bg") que puede matchear cualquier cosa.
        kw = ["cotizacion", "cotización", "bg_fondo", "bg-fondo", "fondo", "template"]
    elif w == "terminos":
        kw = ["terminos", "términos", "bg_terminos", "bg-términos", "condiciones", "terms"]
    elif w == "banco":
        kw = ["banco", "bg_bank", "bg-bank", "datos", "transferencia", "bank"]
    elif w == "logo":
        kw = ["logo"]

    # Preferir el archivo MÁS NUEVO que haga match con la keyword.
    # Si hay duplicados por nombre (Drive lo permite), esto asegura que el último subido gane.
    def mtime(f: DriveFile) -> float:
        return _rfc3339_to_ts(f.modified_time)

    if kw:
        hits = []
        for f in cand:
            n = (f.name or "").lower()
            if any(k in n for k in kw):
                hits.append(f)
        if hits:
            hits.sort(key=mtime, reverse=True)
            return hits[0]

    # Para evitar "cualquier cosa" (p.ej. que logo=portada),
    # si no hay match por keyword, preferimos NO inventar.
    # El llamador puede caer al asset local/DB.
    return None


def _guess_ext(name: str, mime_type: str) -> str:
    n = (name or "").lower().strip()
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        if n.endswith(ext):
            return ext
    mt = (mime_type or "").lower()
    if mt == "image/png":
        return ".png"
    if mt in ("image/jpg", "image/jpeg"):
        return ".jpg"
    if mt == "image/webp":
        return ".webp"
    return ".bin"


def _try_convert_webp_to_png(in_path: Path, out_path: Path) -> bool:
    """
    Intenta convertir WEBP -> PNG con herramientas del sistema si existen.
    (En algunos hostings Pillow no tiene soporte WEBP.)
    """
    candidates = []
    dwebp = shutil.which("dwebp")
    if dwebp:
        candidates.append([dwebp, str(in_path), "-o", str(out_path)])
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        candidates.append([ffmpeg, "-y", "-i", str(in_path), str(out_path)])
    magick = shutil.which("magick")
    if magick:
        candidates.append([magick, "convert", str(in_path), str(out_path)])
    convert = shutil.which("convert")
    if convert:
        candidates.append([convert, str(in_path), str(out_path)])

    for cmd in candidates:
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=20)
            if out_path.exists() and out_path.stat().st_size > 0:
                return True
        except Exception:
            continue
    return False


def _download_file_to_cache(
    file_id: str,
    preferred_name: str,
    refresh: bool,
    src_name: str,
    mime_type: str,
    remote_mtime_ts: float | None = None,
) -> Optional[Path]:
    svc = _drive_service()
    if not svc or not file_id:
        return None

    base = Path(__file__).resolve().parents[2] / "data" / "quote_assets" / "drive_cache"
    base.mkdir(parents=True, exist_ok=True)

    pref = (preferred_name or "").lower()
    is_logo = ("logo" in pref) and ("cotizacion" not in pref) and ("terminos" not in pref) and ("banco" not in pref) and ("portada" not in pref)

    # Preferimos:
    # - fondos (portada/cotizacion/terminos/banco): JPG optimizado y redimensionado A4 para que "vuele".
    # - logo: PNG (mejor nitidez).
    out_bg = base / f"{preferred_name}_{file_id}.jpg"
    out_png = base / f"{preferred_name}_{file_id}.png"
    out_final = out_png if is_logo else out_bg
    if out_final.exists() and (not refresh):
        # Si el archivo en Drive fue reemplazado (misma ID, nuevo contenido),
        # el file_id sigue igual. Usamos modifiedTime para decidir si re-descargar.
        try:
            if remote_mtime_ts is not None:
                local_ts = float(out_final.stat().st_mtime)
                # margen de 2s por filesystem clocks
                if remote_mtime_ts > (local_ts + 2.0):
                    refresh = True
        except Exception:
            pass
        if not refresh:
            return out_final

    try:
        raw = svc.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
        if not raw:
            return None
    except Exception:
        return None

    try:
        from PIL import Image, ImageFile  # type: ignore
        import io

        ImageFile.LOAD_TRUNCATED_IMAGES = True
        try:
            Image.MAX_IMAGE_PIXELS = None  # type: ignore[attr-defined]
        except Exception:
            pass

        im = Image.open(io.BytesIO(raw))
        im.load()
        if is_logo:
            im = im.convert("RGBA")
            tmp = out_png.with_suffix(".tmp")
            im.save(tmp, format="PNG")
            tmp.replace(out_png)
            return out_png

        # Fondo: redimensionar a A4 para acelerar render y bajar peso.
        im = im.convert("RGB")
        if im.size != (_A4_W, _A4_H):
            im = im.resize((_A4_W, _A4_H))
        tmp = out_bg.with_suffix(".tmp")
        im.save(tmp, format="JPEG", quality=_BG_JPEG_QUALITY, optimize=True, progressive=True)
        tmp.replace(out_bg)
        return out_bg
    except Exception as e:
        # fallback: en algunos hostings Pillow no soporta WEBP
        ext = _guess_ext(src_name, mime_type)
        if ext == ".webp":
            tmp_in = base / f".tmp_{preferred_name}_{file_id}.webp"
            tmp_out = base / f".tmp_{preferred_name}_{file_id}.png"
            try:
                tmp_in.write_bytes(raw)
                if _try_convert_webp_to_png(tmp_in, tmp_out):
                    # si era fondo, convertimos a JPG A4 usando Pillow si existe;
                    # si no, dejamos PNG tal cual.
                    if not is_logo:
                        try:
                            from PIL import Image, ImageFile  # type: ignore
                            ImageFile.LOAD_TRUNCATED_IMAGES = True
                            try:
                                Image.MAX_IMAGE_PIXELS = None  # type: ignore[attr-defined]
                            except Exception:
                                pass
                            im = Image.open(tmp_out)
                            im.load()
                            im = im.convert("RGB")
                            if im.size != (_A4_W, _A4_H):
                                im = im.resize((_A4_W, _A4_H))
                            im.save(out_bg, format="JPEG", quality=_BG_JPEG_QUALITY, optimize=True, progressive=True)
                            try:
                                tmp_out.unlink(missing_ok=True)
                            except Exception:
                                pass
                            try:
                                tmp_in.unlink(missing_ok=True)
                            except Exception:
                                pass
                            return out_bg
                        except Exception:
                            pass
                    tmp_out.replace(out_png)
                    try:
                        tmp_in.unlink(missing_ok=True)  # py3.10 supports missing_ok
                    except Exception:
                        pass
                    # Si no pudimos generar JPG, devolvemos PNG.
                    return out_png
            except Exception:
                pass
            _log_once(
                "service",
                "No pude convertir WEBP->PNG en el servidor. Sube assets como PNG/JPG (recomendado) o instala soporte WEBP.",
                every_seconds=300,
            )
            # Guardar el original (sirve para debug; puede no renderizar en PDF).
            out_webp = base / f"{preferred_name}_{file_id}.webp"
            try:
                out_webp.write_bytes(raw)
                return out_webp
            except Exception:
                return None

        # Si ya es PNG/JPG, guardar tal cual con extensión correcta.
        try:
            out_raw = base / f"{preferred_name}_{file_id}{ext if ext != '.bin' else ''}"
            out_raw.write_bytes(raw)
            return out_raw
        except Exception:
            return None


def resolve_brand_assets_from_folder(
    marca: str,
    folder_id: str,
    refresh: bool = False,
    ttl_seconds: int = 600,
) -> Dict[str, str]:
    """
    Retorna file://... para {portada,cotizacion,terminos,banco,logo} si se puede.
    Cache:
    - si refresh=False: usa cache mientras exista (y descarga solo si falta).
    - si refresh=True: re-descarga lo que encuentre como "mejor".
    """
    key = normalize_marca(marca or "")
    if not key or not folder_id:
        return {}

    base_cache = Path(__file__).resolve().parents[2] / "data" / "quote_assets" / "drive_cache"
    try:
        base_cache.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def _pick_from_disk(want: str) -> Optional[Path]:
        # Preferimos JPG (fondos optimizados), pero aceptamos PNG si es lo único.
        pats = [
            f"{key}_{want}_*.jpg",
            f"{key}_{want}_*.jpeg",
            f"{key}_{want}_*.png",
            f"{key}_{want}_*.webp",
        ]
        cand: List[Path] = []
        for pat in pats:
            try:
                cand.extend(list(base_cache.glob(pat)))
            except Exception:
                continue
        cand = [p for p in cand if p.is_file()]
        if not cand:
            return None
        cand.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return cand[0]

    # TTL simple: si no refresh, permitimos no pegarle a Drive siempre (hostings lentos).
    stamp = base_cache / f".stamp_{key}_{folder_id}"
    if not refresh:
        # Si el TTL no expiró, devolvemos desde disco SIN tocar Drive (esto era el cuello de botella).
        try:
            fresh = stamp.exists() and (time.time() - stamp.stat().st_mtime) < float(ttl_seconds)
        except Exception:
            fresh = False
        if fresh:
            out_disk: Dict[str, str] = {}
            for want in ("portada", "cotizacion", "terminos", "banco", "logo"):
                p = _pick_from_disk(want)
                if p and p.exists():
                    out_disk[want] = p.as_uri()
            # Si tenemos al menos los 4 fondos, devolvemos inmediato.
            if all(k in out_disk for k in ("portada", "cotizacion", "terminos", "banco")):
                return out_disk

    files = _list_folder_recursive(folder_id, max_depth=2)
    if not files:
        _log_once("resolve", f"Folder vacío o sin acceso. marca={key} folder_id={folder_id}", every_seconds=180)
        # Último fallback: intenta devolver lo que haya en disco.
        out_disk: Dict[str, str] = {}
        for want in ("portada", "cotizacion", "terminos", "banco", "logo"):
            p = _pick_from_disk(want)
            if p and p.exists():
                out_disk[want] = p.as_uri()
        return out_disk

    out: Dict[str, str] = {}
    for want in ("portada", "cotizacion", "terminos", "banco", "logo"):
        f = _pick_file(files, want)
        if not f:
            continue
        # Si el item es un shortcut, descargamos el target real.
        dl_id = f.target_id if ((f.mime_type or "").lower() == _MIME_SHORTCUT and f.target_id) else f.id
        dl_mt = f.target_mime_type if ((f.mime_type or "").lower() == _MIME_SHORTCUT and f.target_mime_type) else f.mime_type
        remote_ts = _rfc3339_to_ts(f.modified_time)
        p = _download_file_to_cache(
            dl_id,
            f"{key}_{want}",
            refresh=refresh,
            src_name=f.name,
            mime_type=dl_mt,
            remote_mtime_ts=remote_ts if remote_ts > 0 else None,
        )
        if p and p.exists():
            out[want] = p.as_uri()

    if not out:
        # ayuda a diagnosticar: en Drive a veces hay subcarpetas o shortcuts.
        sample = []
        for f in files[:20]:
            sample.append({"name": f.name, "mime": f.mime_type, "t_mime": f.target_mime_type})
        _log_once("resolve", f"No detecté assets imagen en folder. marca={key} folder_id={folder_id} sample={sample}", every_seconds=180)

    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(int(time.time())), encoding="utf-8")
    except Exception:
        pass

    return out
