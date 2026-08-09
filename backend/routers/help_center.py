from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from backend.core.database import engine
from backend.gd_intelligence.permissions import role_key
from backend.routers.auth import get_current_user


router = APIRouter(prefix="/api/help", tags=["help"])


def _rows(query: str, params: dict, user: dict) -> list[dict]:
    role = role_key(user)
    with engine.connect() as conn:
        return [dict(x) for x in conn.execute(text(query), {**params, "role": role}).mappings()]


@router.get("/search")
def search(q: str = Query("", max_length=120), level: str | None = Query(None), user: dict = Depends(get_current_user)):
    term = f"%{q.strip()}%"
    level = level if level in {"BASIC", "INTERMEDIATE", "ADVANCED"} else None
    items = _rows("""
      SELECT a.slug,a.screen_id,a.title,a.summary,a.content,a.level,a.version,a.module_version,a.updated_at,c.name category
      FROM help_articles a LEFT JOIN help_categories c ON c.id=a.category_id
      WHERE a.published AND (CAST(:level AS varchar) IS NULL OR a.level=CAST(:level AS varchar))
        AND (:term='%%' OR a.title ILIKE :term OR a.summary ILIKE :term OR a.content ILIKE :term
          OR EXISTS(SELECT 1 FROM help_keywords k WHERE k.article_id=a.id AND k.keyword ILIKE :term))
        AND (NOT EXISTS(SELECT 1 FROM help_article_roles r WHERE r.article_id=a.id)
          OR EXISTS(SELECT 1 FROM help_article_roles r WHERE r.article_id=a.id AND upper(r.role_key)=upper(:role)))
      ORDER BY c.sort_order,a.level,a.title LIMIT 100
    """, {"term": term, "level": level}, user)
    return {"ok": True, "items": items}


@router.get("/context/{screen_id}")
def context(screen_id: str, user: dict = Depends(get_current_user)):
    items = _rows("""
      SELECT a.slug,a.screen_id,a.title,a.summary,a.content,a.level,a.version,a.module_version,a.updated_at,c.name category
      FROM help_context_links x JOIN help_articles a ON a.id=x.article_id
      LEFT JOIN help_categories c ON c.id=a.category_id
      WHERE x.screen_id=:screen_id AND a.published
        AND (NOT EXISTS(SELECT 1 FROM help_article_roles r WHERE r.article_id=a.id)
          OR EXISTS(SELECT 1 FROM help_article_roles r WHERE r.article_id=a.id AND upper(r.role_key)=upper(:role)))
    """, {"screen_id": screen_id}, user)
    return {"ok": True, "item": items[0] if items else None}
