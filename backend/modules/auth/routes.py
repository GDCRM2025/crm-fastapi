# backend/modules/auth/routes.py
from fastapi import APIRouter, Depends
from backend.core.auth import get_current_user

router = APIRouter()

@router.get("/me")
def me(user=Depends(get_current_user)):
    return user
