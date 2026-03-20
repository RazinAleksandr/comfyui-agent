"""Authentication routes: login and current user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth import (
    create_access_token,
    get_current_user,
    verify_password,
)
from api.deps import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUser(BaseModel):
    username: str
    display_name: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUser


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    db = get_db()
    user = await db.fetchone("SELECT * FROM users WHERE username = ? AND is_active = 1", [body.username])
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    token = create_access_token({"sub": user["username"]})
    return LoginResponse(
        access_token=token,
        user=AuthUser(username=user["username"], display_name=user["display_name"]),
    )


@router.get("/me", response_model=AuthUser)
async def me(user: dict = Depends(get_current_user)):
    return AuthUser(username=user["username"], display_name=user["display_name"])
