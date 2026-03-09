from fastapi import APIRouter, Depends
from backend.routers.auth import get_current_user

router = APIRouter(tags=["me"])

@router.get("/me")
def me(user=Depends(get_current_user)):
    return user

# Aliases legacy/compat (algunos frontends llaman /auth/me)
@router.get("/auth/me")
@router.get("/web/auth/me")
def me_alias(user=Depends(get_current_user)):
    return user
