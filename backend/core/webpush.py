from __future__ import annotations

import base64
import json
import os
import sys
from typing import Any

from sqlalchemy import text

from backend.core.database import engine
from backend.core.chat_push import _users_table as _users_table  # type: ignore
from backend.core.chat_push import _user_cols as _user_cols  # type: ignore

try:
    from pywebpush import webpush, WebPushException  # type: ignore
except Exception:  # pragma: no cover
    webpush = None
    WebPushException = Exception  # type: ignore


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def get_vapid_private_key() -> str | None:
    """
    Returns a file path to the VAPID private key PEM.
    Falls back to `data/vapid_private_key.pem` if present.
    """
    v = (
        os.getenv("CRM_VAPID_PRIVATE_KEY")
        or os.getenv("VAPID_PRIVATE_KEY")
        or os.getenv("VAPID_PRIVATE")
        or ""
    ).strip()
    if v:
        # If relative, try relative to repo root first.
        if not os.path.isabs(v):
            cand = os.path.join(_project_root(), v)
            if os.path.exists(cand):
                return cand
        if os.path.exists(v):
            return v
    fallback = os.path.join(_project_root(), "data", "vapid_private_key.pem")
    return fallback if os.path.exists(fallback) else None


def get_vapid_public_key() -> str | None:
    """
    Returns the VAPID public key in base64url (uncompressed P-256 point).
    If not present in env, derive it from the private key PEM.
    """
    k = (os.getenv("CRM_VAPID_PUBLIC_KEY") or os.getenv("VAPID_PUBLIC_KEY") or os.getenv("VAPID_PUBLIC") or "").strip()
    if k:
        return k

    # Optional: cached public key file (generated once by script / runtime).
    try:
        pub_file = os.path.join(_project_root(), "data", "vapid_public_key.txt")
        if os.path.exists(pub_file):
            txt = ""
            try:
                with open(pub_file, "r", encoding="utf-8") as f:
                    txt = (f.read() or "").strip()
            except Exception:
                txt = ""
            if txt:
                return txt
    except Exception:
        pass

    priv_path = get_vapid_private_key()
    if not priv_path:
        return None

    try:
        from cryptography.hazmat.primitives import serialization  # type: ignore
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # type: ignore
    except Exception:
        return None

    try:
        with open(priv_path, "rb") as f:
            pem = f.read()
        priv = serialization.load_pem_private_key(pem, password=None)
        pub = priv.public_key()  # type: ignore[attr-defined]
        pub_bytes = pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        out = _b64url(pub_bytes)
        # Cache to file for environments where env vars are not loaded reliably.
        try:
            pub_file = os.path.join(_project_root(), "data", "vapid_public_key.txt")
            if out and (not os.path.exists(pub_file)):
                with open(pub_file, "w", encoding="utf-8") as f:
                    f.write(out)
        except Exception:
            pass
        return out
    except Exception:
        return None


def _vapid_subject() -> str:
    return os.getenv("CRM_VAPID_SUBJECT") or os.getenv("VAPID_SUBJECT") or "mailto:soporte@greendiamond.cl"


def ensure_push_tables(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS public.push_subscriptions (
              id_sub BIGSERIAL PRIMARY KEY,
              user_id BIGINT NOT NULL,
              endpoint TEXT NOT NULL,
              p256dh TEXT NOT NULL,
              auth TEXT NOT NULL,
              user_agent TEXT,
              active BOOLEAN NOT NULL DEFAULT TRUE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_push_sub_endpoint ON public.push_subscriptions(endpoint)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_push_sub_user ON public.push_subscriptions(user_id)"))
    conn.execute(text("ALTER TABLE public.push_subscriptions ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE"))
    conn.execute(text("ALTER TABLE public.push_subscriptions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()"))
    conn.execute(text("ALTER TABLE public.push_subscriptions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"))


def save_subscription(
    *,
    user_id: int,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None = None,
) -> None:
    endpoint = (endpoint or "").strip()
    p256dh = (p256dh or "").strip()
    auth = (auth or "").strip()
    if not endpoint or not p256dh or not auth:
        return
    with engine.begin() as cn:
        ensure_push_tables(cn)
        cn.execute(
            text(
                """
                INSERT INTO public.push_subscriptions(user_id, endpoint, p256dh, auth, user_agent, active, created_at, updated_at)
                VALUES (:uid, :ep, :p, :a, :ua, TRUE, now(), now())
                ON CONFLICT (endpoint) DO UPDATE SET
                  user_id=EXCLUDED.user_id,
                  p256dh=EXCLUDED.p256dh,
                  auth=EXCLUDED.auth,
                  user_agent=EXCLUDED.user_agent,
                  active=TRUE,
                  updated_at=now()
                """
            ),
            {"uid": int(user_id), "ep": endpoint, "p": p256dh, "a": auth, "ua": (user_agent or "")[:512]},
        )


def deactivate_subscription(*, endpoint: str, user_id: int | None = None) -> None:
    endpoint = (endpoint or "").strip()
    if not endpoint:
        return
    with engine.begin() as cn:
        ensure_push_tables(cn)
        if user_id is None:
            cn.execute(
                text("UPDATE public.push_subscriptions SET active=FALSE, updated_at=now() WHERE endpoint=:ep"),
                {"ep": endpoint},
            )
        else:
            cn.execute(
                text("UPDATE public.push_subscriptions SET active=FALSE, updated_at=now() WHERE endpoint=:ep AND user_id=:uid"),
                {"ep": endpoint, "uid": int(user_id)},
            )


def _active_subscriptions_for_users(conn, user_ids: list[int]) -> list[dict[str, Any]]:
    if not user_ids:
        return []
    ensure_push_tables(conn)
    rows = conn.execute(
        text(
            """
            SELECT endpoint, p256dh, auth
            FROM public.push_subscriptions
            WHERE active=TRUE AND user_id = ANY(:uids)
            """
        ),
        {"uids": [int(x) for x in user_ids]},
    ).mappings().all()
    return [dict(r) for r in rows]


def user_ids_by_role_contains(*, role_contains: list[str], include_all_active: bool = False) -> list[int]:
    with engine.connect() as cn:
        ut = _users_table(cn)
        if not ut:
            return []
        cols = _user_cols(cn, ut)
        id_col = (
            "id_usuario"
            if "id_usuario" in cols
            else ("id_user" if "id_user" in cols else ("id" if "id" in cols else None))
        )
        if not id_col:
            return []
        sel_rol = "rol" if "rol" in cols else ("role" if "role" in cols else None)
        if not sel_rol and not include_all_active:
            return []

        rol_txt = f"btrim(COALESCE({sel_rol}::text,''))" if sel_rol else "''"
        rol_norm = f"""
          (CASE
            WHEN {rol_txt} ~ '^[0-9]+$' THEN
              (CASE {rol_txt}
                WHEN '1' THEN 'ADMIN'
                WHEN '2' THEN 'EJECUTIVO'
                WHEN '3' THEN 'OPERACIONES'
                WHEN '4' THEN 'BODEGUERO'
                WHEN '5' THEN 'COMPRAS'
                WHEN '6' THEN 'CONDUCTOR'
                WHEN '7' THEN 'OPERADOR'
                WHEN '8' THEN 'MICE'
                WHEN '9' THEN 'OPERADOR PATIO'
                WHEN '11' THEN 'FINANZAS'
                ELSE {rol_txt}
              END)
            ELSE upper({rol_txt})
          END)
        """.strip()

        where = ["1=1"]
        params: dict[str, Any] = {}
        if "is_active" in cols:
            where.append("COALESCE(is_active, TRUE) = TRUE")
        if not include_all_active:
            parts = []
            for i, r in enumerate(role_contains or []):
                k = f"r{i}"
                params[k] = f"%{str(r).upper()}%"
                parts.append(f"COALESCE({rol_norm},'') LIKE :{k}")
            if not parts:
                return []
            where.append("(" + " OR ".join(parts) + ")")

        rows = cn.execute(
            text(f"SELECT {id_col} AS idu FROM {ut} WHERE " + " AND ".join(where)),
            params,
        ).fetchall()
        out: list[int] = []
        for r in rows:
            try:
                out.append(int(r[0]))
            except Exception:
                pass
        return sorted(set(out))


def send_webpush_to_users(
    *,
    user_ids: list[int],
    title: str,
    body: str,
    url: str,
    tag: str = "gd-evento",
) -> dict[str, int]:
    """
    Envía Web Push a usuarios específicos (si existen suscripciones activas).
    Requiere instalar `pywebpush` en el server y configurar VAPID en variables de entorno.
    """
    if webpush is None:
        return {
            "sent": 0,
            "failed": 0,
            "missing_lib": 1,
            "py": sys.executable,
            "py_version": sys.version.split()[0],
        }

    pub = get_vapid_public_key()
    priv = get_vapid_private_key()
    if not pub or not priv:
        return {
            "sent": 0,
            "failed": 0,
            "missing_vapid": 1,
            "has_public": bool(pub),
            "has_private": bool(priv),
        }

    payload = json.dumps(
        {"title": (title or "").strip()[:80], "body": (body or "").strip()[:180], "url": url, "tag": tag},
        ensure_ascii=False,
    )
    sent = 0
    failed = 0
    with engine.begin() as cn:
        subs = _active_subscriptions_for_users(cn, user_ids)
        for s in subs:
            try:
                webpush(
                    subscription_info={
                        "endpoint": s["endpoint"],
                        "keys": {"p256dh": s["p256dh"], "auth": s["auth"]},
                    },
                    data=payload,
                    vapid_private_key=priv,
                    vapid_claims={"sub": _vapid_subject()},
                )
                sent += 1
            except WebPushException:
                failed += 1
                try:
                    deactivate_subscription(endpoint=str(s.get("endpoint") or ""), user_id=None)
                except Exception:
                    pass
            except Exception:
                failed += 1
    return {"sent": sent, "failed": failed}
