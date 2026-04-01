from __future__ import annotations

"""
Chat interno v2:
- Conversaciones (directo)
- Lista de usuarios para iniciar chat
- Presencia

Mantiene endpoints legacy (/chat/messages) para no romper nada viejo, pero el UI nuevo usa threads.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.routers.auth import get_current_user
from pathlib import Path
import re
import secrets
import time

router = APIRouter(prefix="/chat", tags=["chat"])
_DDL_READY = False

def _cols(db: Session, table: str) -> set[str]:
    rows = db.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t
            """
        ),
        {"t": table},
    ).fetchall()
    return {r[0] for r in rows}

def _table_exists(db: Session, table: str) -> bool:
    try:
        return bool(db.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())
    except Exception:
        return False


def _users_table(db: Session) -> str | None:
    # Prefer existing schema name(s). We keep the list tight to avoid any injection risk.
    for t in ("usuarios", "users"):
        if _table_exists(db, t):
            return t
    return None


def _role(me) -> str:
    return str(me.get("role") or me.get("rol") or "").upper()


def _require_chat_access(me) -> None:
    r = _role(me)
    # Chat interno (staff app): permitido para todos los roles.
    # La visibilidad real se controla por membresía de thread (chat_thread_members) y por UI/permissions.
    _ = r


def _user_id(me) -> int:
    """
    Necesitamos un id estable incluso si el auth no entrega id numérico
    (por ejemplo usuarios hardcodeados como 'greengd').
    """
    v = me.get("id") or me.get("id_user") or me.get("id_usuario")
    if str(v or "").isdigit():
        return int(v)
    import zlib

    key = (me.get("username") or me.get("email") or me.get("name") or "user").encode("utf-8")
    return 1_000_000_000 + (zlib.crc32(key) & 0x7FFFFFFF)


def _ensure_tables(db: Session) -> None:
    """
    DDL idempotente con lock para evitar deadlocks en producción.

    Nota: en el hosting (Passenger) pueden entrar requests concurrentes al arrancar,
    y los ALTER TABLE con AccessExclusiveLock pueden deadlockear. Por eso:
    - ejecutamos DDL solo 1 vez por proceso
    - protegemos con pg_try_advisory_lock
    - usamos lock_timeout corto y si no podemos, seguimos sin romper la request
    """
    global _DDL_READY
    if _DDL_READY:
        return

    try:
        got = bool(db.execute(text("SELECT pg_try_advisory_lock(25042026)")).scalar())
    except Exception:
        got = False
    if not got:
        # Otro worker está migrando; no bloqueamos la request.
        return

    try:
        try:
            db.execute(text("SET LOCAL lock_timeout='2s'"))
        except Exception:
            pass

        # threads
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_threads (
                  id_thread SERIAL PRIMARY KEY,
                  kind TEXT NOT NULL DEFAULT 'direct',
                  title TEXT,
                  thread_key TEXT UNIQUE,
                  created_at TIMESTAMP DEFAULT now(),
                  updated_at TIMESTAMP DEFAULT now()
                )
                """
            )
        )

        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_thread_members (
                  id_thread INT NOT NULL REFERENCES chat_threads(id_thread) ON DELETE CASCADE,
                  user_id BIGINT NOT NULL,
                  name TEXT,
                  email TEXT,
                  avatar_url TEXT,
                  joined_at TIMESTAMP DEFAULT now(),
                  PRIMARY KEY (id_thread, user_id)
                )
                """
            )
        )

        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_thread_messages (
                  id_message SERIAL PRIMARY KEY,
                  id_thread INT NOT NULL REFERENCES chat_threads(id_thread) ON DELETE CASCADE,
                  sender_id BIGINT,
                  sender_name TEXT,
                  sender_email TEXT,
                  message TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT now(),
                  attachment_url TEXT,
                  attachment_name TEXT,
                  attachment_type TEXT,
                  attachment_size BIGINT
                )
                """
            )
        )
        db.execute(
            text("CREATE INDEX IF NOT EXISTS chat_thread_messages_thread_id_idx ON chat_thread_messages(id_thread, id_message)")
        )

        # presence v2 (BIGINT para soportar hash ids)
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_presence2 (
                  user_id BIGINT PRIMARY KEY,
                  name TEXT,
                  email TEXT,
                  avatar_url TEXT,
                  updated_at TIMESTAMP DEFAULT now()
                )
                """
            )
        )

        # legacy board tables (si no existen, las creamos para compat)
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                  id_message SERIAL PRIMARY KEY,
                  sender_id INT,
                  sender_name TEXT,
                  sender_email TEXT,
                  message TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT now()
                )
                """
            )
        )
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS chat_presence (
                  id_user INT,
                  name TEXT,
                  email TEXT,
                  updated_at TIMESTAMP DEFAULT now(),
                  PRIMARY KEY (id_user)
                )
                """
            )
        )

        db.commit()
        _DDL_READY = True
    finally:
        try:
            db.execute(text("SELECT pg_advisory_unlock(25042026)"))
            db.commit()
        except Exception:
            pass


def _ensure_default_groups(db: Session) -> None:
    """
    Grupos predeterminados (idempotente) para operación.

    Reglas pedidas:
    - Operadores/Conductores + Operaciones + Oscar Mendoza
    - MICE + Operaciones + Super Admin
    - Operaciones + Ejecutivos de Ventas
    - Operadores
    - Conductores
    """
    try:
        ut = _users_table(db)
        if not ut:
            return

        cols_u = _cols(db, ut)
        # Campos mínimos
        id_col = "id_usuario" if "id_usuario" in cols_u else ("id" if "id" in cols_u else None)
        if not id_col:
            return

        sel_name = "COALESCE(NULLIF(nombre,''), NULLIF(username,''), NULLIF(email,''), 'Usuario') AS name" if ("nombre" in cols_u or "username" in cols_u or "email" in cols_u) else "'Usuario' AS name"
        sel_email = "COALESCE(email,'') AS email" if "email" in cols_u else "'' AS email"
        sel_username = "COALESCE(username,'') AS username" if "username" in cols_u else "'' AS username"
        sel_role = "COALESCE(rol,'') AS rol" if "rol" in cols_u else "'' AS rol"
        sel_avatar = "COALESCE(avatar_url,'') AS avatar_url" if "avatar_url" in cols_u else "'' AS avatar_url"
        where = ["1=1"]
        if "is_active" in cols_u:
            where.append("COALESCE(is_active, TRUE) = TRUE")

        rows = db.execute(
            text(
                f"""
                SELECT
                  {id_col}::bigint AS id_usuario,
                  {sel_name},
                  {sel_email},
                  {sel_username},
                  {sel_role},
                  {sel_avatar}
                FROM {ut}
                WHERE {' AND '.join(where)}
                """
            )
        ).mappings().all()

        def role_up(r: str) -> str:
            return str(r or "").strip().upper()

        def has_any(r: str, keys: list[str]) -> bool:
            ru = role_up(r)
            return any(k in ru for k in keys)

        OPS_KEYS = ["OPERACION", "OPERACIONES", "JEFE DE OPERACIONES"]
        MICE_KEYS = ["MICE"]
        SALES_KEYS = ["EJECUTIV", "VENTAS"]
        OPER_KEYS = ["OPERADOR"]
        COND_KEYS = ["CONDUCTOR", "CHOFER", "CHOP"]
        SUPER_KEYS = ["SUPERADMIN"]

        all_users = []
        for r in rows:
            try:
                uid = int(r.get("id_usuario"))
            except Exception:
                continue
            all_users.append(
                {
                    "id": uid,
                    "name": str(r.get("name") or "").strip(),
                    "email": str(r.get("email") or "").strip(),
                    "username": str(r.get("username") or "").strip().lower(),
                    "rol": str(r.get("rol") or ""),
                    "avatar_url": (str(r.get("avatar_url") or "").strip() or None),
                }
            )

        def pick_ids(pred) -> set[int]:
            out: set[int] = set()
            for u in all_users:
                if pred(u):
                    out.add(int(u["id"]))
            return out

        ops_ids = pick_ids(lambda u: has_any(u["rol"], OPS_KEYS))
        mice_ids = pick_ids(lambda u: has_any(u["rol"], MICE_KEYS))
        sales_ids = pick_ids(lambda u: has_any(u["rol"], SALES_KEYS))
        oper_ids = pick_ids(lambda u: has_any(u["rol"], OPER_KEYS))
        cond_ids = pick_ids(lambda u: has_any(u["rol"], COND_KEYS))
        super_ids = pick_ids(lambda u: has_any(u["rol"], SUPER_KEYS))

        # Oscar (por username/email) — siempre incluido en el grupo staff/ops.
        oscar_ids = pick_ids(lambda u: u["username"] in {"greengd", "oscarmendoza"} or u["email"].lower() == "oscarmendoza@greendiamond.cl")

        groups = [
            {
                "key": "grp:staff_ops_oscar",
                "title": "Operadores/Conductores + Operaciones",
                "ids": (oper_ids | cond_ids | ops_ids | oscar_ids),
            },
            {
                "key": "grp:mice_ops_super",
                "title": "MICE + Operaciones",
                "ids": (mice_ids | ops_ids | super_ids),
            },
            {
                "key": "grp:ops_sales",
                "title": "Operaciones + Ventas",
                "ids": (ops_ids | sales_ids),
            },
            {
                "key": "grp:operadores",
                "title": "Operadores",
                "ids": (oper_ids | super_ids),
            },
            {
                "key": "grp:conductores",
                "title": "Conductores",
                "ids": (cond_ids | super_ids),
            },
        ]

        for g in groups:
            ids = set(int(x) for x in (g.get("ids") or set()) if int(x) > 0)
            if not ids:
                continue

            th = db.execute(
                text("SELECT id_thread FROM chat_threads WHERE thread_key=:k LIMIT 1"),
                {"k": str(g["key"])},
            ).scalar()
            if not th:
                th = db.execute(
                    text(
                        """
                        INSERT INTO chat_threads(kind,title,thread_key,created_at,updated_at)
                        VALUES ('group', :t, :k, now(), now())
                        RETURNING id_thread
                        """
                    ),
                    {"t": str(g["title"]), "k": str(g["key"])},
                ).scalar()

            id_thread = int(th)

            # Sync membresía: insert faltantes y remover sobrantes.
            cur = db.execute(
                text("SELECT user_id FROM chat_thread_members WHERE id_thread=:t"),
                {"t": id_thread},
            ).fetchall()
            cur_ids = {int(r[0]) for r in cur}

            to_add = ids - cur_ids
            to_del = cur_ids - ids

            if to_del:
                db.execute(
                    text("DELETE FROM chat_thread_members WHERE id_thread=:t AND user_id = ANY(CAST(:ids AS bigint[]))"),
                    {"t": id_thread, "ids": list(to_del)},
                )

            if to_add:
                for uid in sorted(to_add):
                    u = next((x for x in all_users if int(x["id"]) == int(uid)), None)
                    if not u:
                        continue
                    db.execute(
                        text(
                            """
                            INSERT INTO chat_thread_members(id_thread,user_id,name,email,avatar_url,joined_at)
                            VALUES (:t,:u,:n,:e,:a,now())
                            ON CONFLICT DO NOTHING
                            """
                        ),
                        {
                            "t": id_thread,
                            "u": int(uid),
                            "n": str(u.get("name") or ""),
                            "e": str(u.get("email") or ""),
                            "a": u.get("avatar_url"),
                        },
                    )

        db.execute(text("UPDATE chat_threads SET updated_at=now() WHERE kind='group' AND thread_key LIKE 'grp:%'"))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


@router.get("/users")
def list_users(
    q: str | None = None,
    limit: int = 60,
    include_staff: int = 0,
    db: Session = Depends(get_db),
    me=Depends(get_current_user),
):
    _require_chat_access(me)
    _ensure_tables(db)
    ut = _users_table(db)
    if not ut:
        return {"ok": True, "items": []}
    q = (q or "").strip().lower()
    lim = max(1, min(200, int(limit)))
    cols_u = _cols(db, ut)
    where = ["1=1"]
    # si existe is_active, filtramos, pero tratamos NULL como TRUE (no queremos esconder usuarios por data incompleta)
    if "is_active" in cols_u:
        where.append("COALESCE(is_active, TRUE) = TRUE")
    # Por defecto el CRM oculta operadores/choferes para no ensuciar el listado.
    # Staff app puede pedir include_staff=1.
    if (not int(include_staff)) and ("rol" in cols_u):
        where.append("COALESCE(upper(rol),'') NOT LIKE '%OPERADOR%'")
        where.append("COALESCE(upper(rol),'') NOT LIKE '%CONDUCTOR%'")
        where.append("COALESCE(upper(rol),'') NOT LIKE '%CHOFER%'")
    params = {"lim": lim}
    if q:
        parts = []
        if "nombre" in cols_u:
            parts.append("lower(nombre) LIKE :q")
        if "email" in cols_u:
            parts.append("lower(email) LIKE :q")
        if "username" in cols_u:
            parts.append("lower(username) LIKE :q")
        if parts:
            where.append("(" + " OR ".join(parts) + ")")
        params["q"] = f"%{q}%"

    id_col = "id_usuario" if "id_usuario" in cols_u else ("id_user" if "id_user" in cols_u else ("id" if "id" in cols_u else None))
    if not id_col:
        return {"ok": True, "items": []}

    sel_nombre = "nombre" if "nombre" in cols_u else "NULL::text"
    sel_email = "email" if "email" in cols_u else "NULL::text"
    sel_username = "username" if "username" in cols_u else "NULL::text"
    sel_rol = "rol" if "rol" in cols_u else "NULL::text"
    sel_avatar = "avatar_url" if "avatar_url" in cols_u else "NULL::text"
    order = "nombre" if "nombre" in cols_u else id_col
    rows = db.execute(
        text(
            f"""
            SELECT
              {id_col} AS id_usuario,
              {sel_nombre} AS nombre,
              {sel_email} AS email,
              {sel_username} AS username,
              {sel_rol} AS rol,
              {sel_avatar} AS avatar_url
            FROM {ut}
            WHERE {' AND '.join(where)}
            ORDER BY {order} ASC
            LIMIT :lim
            """
        ),
        params,
    ).mappings().all()
    items = []
    for r in rows:
        items.append(
            {
                "id": int(r.get("id_usuario")),
                "name": r.get("nombre") or r.get("username") or "",
                "email": r.get("email") or "",
                "role": r.get("rol") or "",
                "avatar_url": r.get("avatar_url"),
            }
        )
    return {"ok": True, "items": items}


@router.get("/threads")
def threads(db: Session = Depends(get_db), me=Depends(get_current_user), limit: int = 60):
    _require_chat_access(me)
    _ensure_tables(db)
    _ensure_default_groups(db)
    uid = _user_id(me)
    lim = max(1, min(200, int(limit)))
    try:
        # Importante: evitamos usar alias (last_at) dentro de COALESCE en el mismo SELECT
        # porque en algunos PGs/planes puede dar error. Lo resolvemos con subselect.
        rows = db.execute(
            text(
                """
                WITH q AS (
                  SELECT
                    t.id_thread,
                    t.kind,
                    COALESCE(
                      NULLIF(t.title,''),
                      (SELECT tm2.name
                       FROM chat_thread_members tm2
                       WHERE tm2.id_thread=t.id_thread AND tm2.user_id<>:uid
                       ORDER BY tm2.user_id
                       LIMIT 1),
                      'Chat'
                    ) AS title,
                    t.updated_at,
                    (SELECT tm2.user_id
                     FROM chat_thread_members tm2
                     WHERE tm2.id_thread=t.id_thread AND tm2.user_id<>:uid
                     ORDER BY tm2.user_id
                     LIMIT 1) AS other_user_id,
                    (SELECT tm2.email
                     FROM chat_thread_members tm2
                     WHERE tm2.id_thread=t.id_thread AND tm2.user_id<>:uid
                     ORDER BY tm2.user_id
                     LIMIT 1) AS other_email,
                    (SELECT tm2.avatar_url
                     FROM chat_thread_members tm2
                     WHERE tm2.id_thread=t.id_thread AND tm2.user_id<>:uid
                     ORDER BY tm2.user_id
                     LIMIT 1) AS other_avatar_url,
                    (SELECT COUNT(*) FROM chat_thread_members tm3 WHERE tm3.id_thread=t.id_thread) AS members,
                    (SELECT m.id_message FROM chat_thread_messages m WHERE m.id_thread=t.id_thread ORDER BY m.id_message DESC LIMIT 1) AS last_message_id,
                    (SELECT m.created_at FROM chat_thread_messages m WHERE m.id_thread=t.id_thread ORDER BY m.id_message DESC LIMIT 1) AS last_at,
                    (SELECT m.sender_id FROM chat_thread_messages m WHERE m.id_thread=t.id_thread ORDER BY m.id_message DESC LIMIT 1) AS last_sender_id,
                    (SELECT m.sender_name FROM chat_thread_messages m WHERE m.id_thread=t.id_thread ORDER BY m.id_message DESC LIMIT 1) AS last_sender_name,
                    (SELECT m.sender_email FROM chat_thread_messages m WHERE m.id_thread=t.id_thread ORDER BY m.id_message DESC LIMIT 1) AS last_sender_email,
                    (SELECT m.message FROM chat_thread_messages m WHERE m.id_thread=t.id_thread ORDER BY m.id_message DESC LIMIT 1) AS last_msg
                  FROM chat_threads t
                  JOIN chat_thread_members tm ON tm.id_thread=t.id_thread
                  WHERE tm.user_id=:uid
                )
                SELECT *
                FROM q
                ORDER BY COALESCE(q.last_at, q.updated_at) DESC NULLS LAST, q.id_thread DESC
                LIMIT :lim
                """
            ),
            {"uid": uid, "lim": lim},
        ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}
    except Exception as e:
        # Log a disco para debugging en prod.
        import secrets, traceback
        from pathlib import Path
        rid = secrets.token_hex(4)
        try:
            root = Path(__file__).resolve().parents[2]
            dbg = root / "data" / "debug"
            dbg.mkdir(parents=True, exist_ok=True)
            (dbg / "chat_500.log").open("a", encoding="utf-8").write(
                f"=== RID={rid} where=threads uid={uid} ===\n{traceback.format_exc()}\n"
            )
        except Exception:
            pass
        raise HTTPException(500, f"Internal Server Error (chat). RID={rid}")


@router.post("/threads/group")
def create_group(body: dict, db: Session = Depends(get_db), me=Depends(get_current_user)):
    """
    Crea un chat grupal con título y miembros.
    body: { title: str, user_ids: [int, ...] }
    """
    _require_chat_access(me)
    _ensure_tables(db)
    import uuid

    uid = _user_id(me)
    title = (body.get("title") or "").strip() or "Grupo"
    raw_ids = body.get("user_ids") or []
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "user_ids debe ser lista")
    ids = []
    for x in raw_ids:
        if str(x or "").isdigit():
            i = int(x)
            if i not in ids:
                ids.append(i)
    # al menos 2 participantes además de mí
    ids = [i for i in ids if i != uid]
    if len(ids) < 2:
        raise HTTPException(400, "Selecciona al menos 2 usuarios para un grupo")

    key = f"group:{uuid.uuid4().hex}"
    new_id = db.execute(
        text(
            """
            INSERT INTO chat_threads(kind,title,thread_key,created_at,updated_at)
            VALUES ('group', :t, :k, now(), now())
            RETURNING id_thread
            """
        ),
        {"t": title, "k": key},
    ).scalar()
    if not new_id:
        raise HTTPException(500, "No pude crear grupo")

    me_name = me.get("name") or me.get("username") or "Usuario"
    me_email = me.get("email") or ""
    db.execute(
        text(
            """
            INSERT INTO chat_thread_members(id_thread,user_id,name,email,avatar_url)
            VALUES (:th,:uid,:n,:e,NULL)
            ON CONFLICT (id_thread,user_id) DO NOTHING
            """
        ),
        {"th": int(new_id), "uid": uid, "n": me_name, "e": me_email},
    )

    ut = _users_table(db)
    if not ut:
        raise HTTPException(500, "No existe tabla de usuarios")
    cols_u = _cols(db, ut)
    id_col = "id_usuario" if "id_usuario" in cols_u else ("id_user" if "id_user" in cols_u else ("id" if "id" in cols_u else None))
    if not id_col:
        raise HTTPException(500, "Tabla de usuarios sin id")
    sel_nombre = "nombre" if "nombre" in cols_u else "NULL::text"
    sel_email = "email" if "email" in cols_u else "NULL::text"
    sel_avatar = "avatar_url" if "avatar_url" in cols_u else "NULL::text"
    members = db.execute(
        text(
            f"""
            SELECT {id_col} AS id_usuario, {sel_nombre} AS nombre, {sel_email} AS email, {sel_avatar} AS avatar_url
            FROM {ut}
            WHERE {id_col} = ANY(:ids)
            """
        ),
        {"ids": ids},
    ).mappings().all()
    for m in members:
        db.execute(
            text(
                """
                INSERT INTO chat_thread_members(id_thread,user_id,name,email,avatar_url)
                VALUES (:th,:uid,:n,:e,:a)
                ON CONFLICT (id_thread,user_id) DO NOTHING
                """
            ),
            {
                "th": int(new_id),
                "uid": int(m.get("id_usuario")),
                "n": (m.get("nombre") or "").strip(),
                "e": (m.get("email") or "").strip(),
                "a": m.get("avatar_url"),
            },
        )
    db.commit()
    return {"ok": True, "id_thread": int(new_id)}


@router.post("/threads/direct")
def create_direct(body: dict, db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_chat_access(me)
    _ensure_tables(db)
    uid = _user_id(me)
    target = body.get("user_id")
    if not str(target or "").isdigit():
        raise HTTPException(400, "user_id requerido")
    tid = int(target)
    if tid == uid:
        raise HTTPException(400, "No puedes chatear contigo mismo")
    a, b = (uid, tid) if uid < tid else (tid, uid)
    key = f"direct:{a}:{b}"

    existing = db.execute(text("SELECT id_thread FROM chat_threads WHERE thread_key=:k LIMIT 1"), {"k": key}).first()
    if existing:
        return {"ok": True, "id_thread": int(existing[0])}

    ut = _users_table(db)
    if not ut:
        raise HTTPException(500, "No existe tabla de usuarios")
    cols_u = _cols(db, ut)
    id_col = "id_usuario" if "id_usuario" in cols_u else ("id_user" if "id_user" in cols_u else ("id" if "id" in cols_u else None))
    if not id_col:
        raise HTTPException(500, "Tabla de usuarios sin id")

    # title = nombre del otro usuario (para lista)
    title = None
    if "nombre" in cols_u:
        tr = db.execute(text(f"SELECT nombre FROM {ut} WHERE {id_col}=:i LIMIT 1"), {"i": tid}).mappings().first()
    else:
        tr = None
    if tr:
        title = tr.get("nombre")

    new_id = db.execute(
        text(
            """
            INSERT INTO chat_threads(kind,title,thread_key,created_at,updated_at)
            VALUES ('direct', :t, :k, now(), now())
            RETURNING id_thread
            """
        ),
        {"t": title, "k": key},
    ).scalar()
    if not new_id:
        raise HTTPException(500, "No pude crear chat")

    me_name = me.get("name") or me.get("username") or "Usuario"
    me_email = me.get("email") or ""
    db.execute(
        text(
            """
            INSERT INTO chat_thread_members(id_thread,user_id,name,email,avatar_url)
            VALUES (:th,:uid,:n,:e,NULL)
            ON CONFLICT (id_thread,user_id) DO NOTHING
            """
        ),
        {"th": int(new_id), "uid": uid, "n": me_name, "e": me_email},
    )
    sel_nombre = "nombre" if "nombre" in cols_u else "NULL::text"
    sel_email = "email" if "email" in cols_u else "NULL::text"
    sel_avatar = "avatar_url" if "avatar_url" in cols_u else "NULL::text"
    tr2 = db.execute(
        text(f"SELECT {sel_nombre} AS nombre, {sel_email} AS email, {sel_avatar} AS avatar_url FROM {ut} WHERE {id_col}=:i LIMIT 1"),
        {"i": tid},
    ).mappings().first()
    db.execute(
        text(
            """
            INSERT INTO chat_thread_members(id_thread,user_id,name,email,avatar_url)
            VALUES (:th,:uid,:n,:e,:a)
            ON CONFLICT (id_thread,user_id) DO NOTHING
            """
        ),
        {
            "th": int(new_id),
            "uid": tid,
            "n": (tr2.get("nombre") if tr2 else "") or "",
            "e": (tr2.get("email") if tr2 else "") or "",
            "a": (tr2.get("avatar_url") if tr2 else None),
        },
    )
    db.commit()
    return {"ok": True, "id_thread": int(new_id)}


@router.get("/threads/{id_thread}/messages")
def thread_messages(id_thread: int, limit: int = 250, db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_chat_access(me)
    _ensure_tables(db)
    uid = _user_id(me)
    ok = db.execute(text("SELECT 1 FROM chat_thread_members WHERE id_thread=:t AND user_id=:u"), {"t": id_thread, "u": uid}).first()
    if not ok:
        raise HTTPException(403, "Sin acceso a este chat")
    lim = max(1, min(1000, int(limit)))
    rows = db.execute(
        text(
            """
            SELECT id_message, id_thread, sender_id, sender_name, sender_email, message,
                   attachment_url, attachment_name, attachment_type, attachment_size,
                   created_at
            FROM chat_thread_messages
            WHERE id_thread=:t
            ORDER BY id_message DESC
            LIMIT :lim
            """
        ),
        {"t": id_thread, "lim": lim},
    ).mappings().all()
    items = list(reversed([dict(r) for r in rows]))
    return {"ok": True, "items": items}


@router.post("/threads/{id_thread}/messages")
def thread_send(id_thread: int, body: dict, db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_chat_access(me)
    _ensure_tables(db)
    uid = _user_id(me)
    msg = (body.get("message") or "").strip()
    att_url = (body.get("attachment_url") or "").strip()
    att_name = (body.get("attachment_name") or "").strip()
    att_type = (body.get("attachment_type") or "").strip()
    att_size = body.get("attachment_size")
    if not msg and not att_url:
        raise HTTPException(400, "mensaje requerido")
    ok = db.execute(text("SELECT 1 FROM chat_thread_members WHERE id_thread=:t AND user_id=:u"), {"t": id_thread, "u": uid}).first()
    if not ok:
        raise HTTPException(403, "Sin acceso a este chat")
    db.execute(
        text(
            """
            INSERT INTO chat_thread_messages(
              id_thread,sender_id,sender_name,sender_email,message,
              attachment_url, attachment_name, attachment_type, attachment_size
            )
            VALUES (:t,:id,:n,:e,:m,:au,:an,:at,:as)
            """
        ),
        {
            "t": id_thread,
            "id": uid,
            "n": me.get("name") or me.get("username") or "Usuario",
            "e": me.get("email") or "",
            "m": msg,
            "au": att_url or None,
            "an": att_name or None,
            "at": att_type or None,
            "as": int(att_size) if str(att_size or "").isdigit() else None,
        },
    )
    db.execute(text("UPDATE chat_threads SET updated_at=now() WHERE id_thread=:t"), {"t": id_thread})
    db.commit()
    return {"ok": True}


@router.post("/threads/{id_thread}/upload")
async def thread_upload(id_thread: int, archivo: UploadFile = File(...), db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_chat_access(me)
    _ensure_tables(db)
    uid = _user_id(me)
    ok = db.execute(text("SELECT 1 FROM chat_thread_members WHERE id_thread=:t AND user_id=:u"), {"t": id_thread, "u": uid}).first()
    if not ok:
        raise HTTPException(403, "Sin acceso a este chat")

    if not archivo or not archivo.filename:
        raise HTTPException(400, "archivo requerido")

    # Allowlist simple (seguro por defecto).
    max_bytes = 12 * 1024 * 1024  # 12MB
    ct = (archivo.content_type or "").lower().strip()
    allowed_ct = {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if ct and (ct not in allowed_ct) and (not ct.startswith("image/")):
        raise HTTPException(400, f"Tipo no permitido: {ct}")

    orig = str(archivo.filename)
    orig = orig.split("/")[-1].split("\\")[-1]
    orig = orig.strip()[:180] or "archivo"
    # sanitize
    safe_base = re.sub(r"[^a-zA-Z0-9._ -]+", "_", orig).strip().replace(" ", "_")
    if not safe_base:
        safe_base = "archivo"
    ext = ""
    if "." in safe_base:
        ext = "." + safe_base.split(".")[-1].lower()[:8]
        safe_base = safe_base.rsplit(".", 1)[0]
    if ext and not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        ext = ""

    stamp = time.strftime("%Y%m%d-%H%M%S")
    tok = secrets.token_hex(6)
    stored = f"{safe_base[:40]}_{stamp}_{tok}{ext}"

    root = Path(__file__).resolve().parents[2]
    out_dir = root / "web" / "uploads" / "chat" / str(int(id_thread))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / stored

    # copy with size limit
    total = 0
    with open(out_path, "wb") as f:
        while True:
            chunk = await archivo.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                try:
                    f.close()
                except Exception:
                    pass
                try:
                    out_path.unlink(missing_ok=True)  # type: ignore[arg-type]
                except Exception:
                    pass
                raise HTTPException(400, f"Archivo demasiado grande (máx {max_bytes//1024//1024}MB)")
            f.write(chunk)

    file_url = f"/web/uploads/chat/{int(id_thread)}/{stored}"
    return {
        "ok": True,
        "url": file_url,
        "name": orig,
        "content_type": ct,
        "size": total,
    }


@router.post("/heartbeat")
def heartbeat(db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_chat_access(me)
    _ensure_tables(db)
    uid = _user_id(me)
    db.execute(
        text(
            """
            INSERT INTO chat_presence2(user_id,name,email,avatar_url,updated_at)
            VALUES (:id,:n,:e,:a,now())
            ON CONFLICT (user_id) DO UPDATE
              SET name=EXCLUDED.name,
                  email=EXCLUDED.email,
                  avatar_url=EXCLUDED.avatar_url,
                  updated_at=now()
            """
        ),
        {
            "id": uid,
            "n": me.get("name") or me.get("username") or "Usuario",
            "e": me.get("email") or "",
            "a": me.get("avatar_url"),
        },
    )
    db.commit()
    return {"ok": True}


@router.get("/presence")
def presence(db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_chat_access(me)
    _ensure_tables(db)
    rows = db.execute(
        text(
            """
            SELECT user_id, name, email, avatar_url, updated_at
            FROM chat_presence2
            WHERE updated_at >= now() - interval '5 minutes'
            ORDER BY updated_at DESC
            """
        )
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


# ----------------------
# Legacy board endpoints (compat)
# ----------------------
@router.get("/messages")
def list_messages(limit: int = 100, db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_chat_access(me)
    _ensure_tables(db)
    rows = db.execute(
        text(
            """
            SELECT id_message, sender_id, sender_name, sender_email, message, created_at
            FROM chat_messages
            ORDER BY id_message DESC
            LIMIT :lim
            """
        ),
        {"lim": max(1, min(500, int(limit)))},
    ).mappings().all()
    items = list(reversed([dict(r) for r in rows]))
    return {"ok": True, "items": items}


@router.post("/messages")
def create_message(body: dict, db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_chat_access(me)
    _ensure_tables(db)
    msg = (body.get("message") or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="mensaje requerido")
    db.execute(
        text(
            """
            INSERT INTO chat_messages(sender_id, sender_name, sender_email, message)
            VALUES (:id, :name, :email, :msg)
            """
        ),
        {
            "id": _user_id(me),
            "name": me.get("name") or me.get("username") or "Usuario",
            "email": me.get("email") or "",
            "msg": msg,
        },
    )
    db.commit()
    return {"ok": True}
