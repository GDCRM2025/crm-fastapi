from __future__ import annotations

from sqlalchemy import text


def _ensure_chat_tables(conn) -> None:
    # Minimal subset (same as backend/routers/chat.py) using SQLAlchemy Core connection.
    conn.execute(
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
    conn.execute(text("ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'direct'"))
    conn.execute(text("ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS title TEXT"))
    conn.execute(text("ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS thread_key TEXT"))
    conn.execute(text("ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()"))
    conn.execute(text("ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now()"))

    conn.execute(
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
    conn.execute(text("ALTER TABLE chat_thread_members ADD COLUMN IF NOT EXISTS name TEXT"))
    conn.execute(text("ALTER TABLE chat_thread_members ADD COLUMN IF NOT EXISTS email TEXT"))
    conn.execute(text("ALTER TABLE chat_thread_members ADD COLUMN IF NOT EXISTS avatar_url TEXT"))
    conn.execute(text("ALTER TABLE chat_thread_members ADD COLUMN IF NOT EXISTS joined_at TIMESTAMP DEFAULT now()"))

    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS chat_thread_messages (
              id_message SERIAL PRIMARY KEY,
              id_thread INT NOT NULL REFERENCES chat_threads(id_thread) ON DELETE CASCADE,
              sender_id BIGINT,
              sender_name TEXT,
              sender_email TEXT,
              message TEXT NOT NULL,
              created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    conn.execute(text("ALTER TABLE chat_thread_messages ADD COLUMN IF NOT EXISTS sender_id BIGINT"))
    conn.execute(text("ALTER TABLE chat_thread_messages ADD COLUMN IF NOT EXISTS sender_name TEXT"))
    conn.execute(text("ALTER TABLE chat_thread_messages ADD COLUMN IF NOT EXISTS sender_email TEXT"))
    conn.execute(text("ALTER TABLE chat_thread_messages ADD COLUMN IF NOT EXISTS message TEXT"))
    conn.execute(text("ALTER TABLE chat_thread_messages ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now()"))
    conn.execute(text("ALTER TABLE chat_thread_messages ADD COLUMN IF NOT EXISTS attachment_url TEXT"))
    conn.execute(text("ALTER TABLE chat_thread_messages ADD COLUMN IF NOT EXISTS attachment_name TEXT"))
    conn.execute(text("ALTER TABLE chat_thread_messages ADD COLUMN IF NOT EXISTS attachment_type TEXT"))
    conn.execute(text("ALTER TABLE chat_thread_messages ADD COLUMN IF NOT EXISTS attachment_size BIGINT"))
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS chat_thread_messages_thread_id_idx ON chat_thread_messages(id_thread, id_message)"
        )
    )


def _users_table(conn) -> str | None:
    for t in ("usuarios", "users"):
        ok = conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{t}"}).scalar()
        if ok:
            return t
    return None


def _user_cols(conn, table: str) -> set[str]:
    rows = conn.execute(
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


def ensure_group_thread(conn, *, thread_key: str, title: str) -> int:
    _ensure_chat_tables(conn)
    row = conn.execute(text("SELECT id_thread FROM chat_threads WHERE thread_key=:k LIMIT 1"), {"k": thread_key}).first()
    if row:
        return int(row[0])
    new_id = conn.execute(
        text(
            """
            INSERT INTO chat_threads(kind,title,thread_key,created_at,updated_at)
            VALUES ('group', :t, :k, now(), now())
            RETURNING id_thread
            """
        ),
        {"t": title, "k": thread_key},
    ).scalar()
    return int(new_id)


def add_members_by_role_contains(
    conn,
    *,
    id_thread: int,
    role_contains: list[str],
    include_all_active: bool = False,
) -> int:
    """
    Agrega al thread a usuarios activos por rol (contiene) o a todos (include_all_active).
    Best-effort: si no hay tabla usuarios, no agrega.
    """
    ut = _users_table(conn)
    if not ut:
        return 0
    cols = _user_cols(conn, ut)
    id_col = "id_usuario" if "id_usuario" in cols else ("id_user" if "id_user" in cols else ("id" if "id" in cols else None))
    if not id_col:
        return 0
    sel_nombre = "nombre" if "nombre" in cols else ("name" if "name" in cols else "NULL::text")
    sel_email = "email" if "email" in cols else "NULL::text"
    sel_avatar = "avatar_url" if "avatar_url" in cols else "NULL::text"
    sel_rol = "rol" if "rol" in cols else ("role" if "role" in cols else "NULL::text")
    rol_txt = f"btrim(COALESCE({sel_rol}::text,''))"
    # Normaliza roles numéricos (legacy) a texto para poder hacer matching por nombre.
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
    if "is_active" in cols:
        where.append("COALESCE(is_active, TRUE) = TRUE")
    params: dict = {"t": int(id_thread)}
    if not include_all_active:
        parts = []
        for i, r in enumerate(role_contains or []):
            k = f"r{i}"
            params[k] = f"%{str(r).upper()}%"
            parts.append(f"COALESCE({rol_norm},'') LIKE :{k}")
        if parts:
            where.append("(" + " OR ".join(parts) + ")")
        else:
            return 0

    rows = conn.execute(
        text(
            f"""
            SELECT {id_col} AS id_usuario, {sel_nombre} AS nombre, {sel_email} AS email, {sel_avatar} AS avatar_url
            FROM {ut}
            WHERE {' AND '.join(where)}
            """
        ),
        params,
    ).mappings().all()

    inserted = 0
    for u in rows:
        conn.execute(
            text(
                """
                INSERT INTO chat_thread_members(id_thread,user_id,name,email,avatar_url)
                VALUES (:t,:uid,:n,:e,:a)
                ON CONFLICT (id_thread,user_id) DO NOTHING
                """
            ),
            {
                "t": int(id_thread),
                "uid": int(u.get("id_usuario")),
                "n": (u.get("nombre") or "").strip(),
                "e": (u.get("email") or "").strip(),
                "a": u.get("avatar_url"),
            },
        )
        inserted += 1
    return inserted


def push_system_message(conn, *, id_thread: int, message: str) -> None:
    _ensure_chat_tables(conn)
    msg = (message or "").strip()
    if not msg:
        return
    conn.execute(
        text(
            """
            INSERT INTO chat_thread_messages(id_thread,sender_id,sender_name,sender_email,message,created_at)
            VALUES (:t, NULL, 'Sistema', 'system@greendiamond', :m, now())
            """
        ),
        {"t": int(id_thread), "m": msg},
    )
    conn.execute(text("UPDATE chat_threads SET updated_at=now() WHERE id_thread=:t"), {"t": int(id_thread)})
