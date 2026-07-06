"""Register, login, and current-user profile (JWT when auth is enabled).

When ``AUTH_DISABLED=true``, register/login are rejected; the UI still calls ``/auth/me``
with the implicit dev user from ``get_current_user_id``.
"""

from fastapi import APIRouter, Depends, HTTPException

from backend.db import get_db
from backend.deps import get_current_user_id
from backend.models.requests import LoginBody, RegisterBody, TokenResponse
from backend.models.test_case import User
from backend.repositories import user_repo
from backend.services.auth_service import create_access_token, hash_password, verify_password

router = APIRouter(tags=["auth"])


@router.post("/auth/register", response_model=TokenResponse)
async def register(body: RegisterBody) -> TokenResponse:
    from backend.config import get_settings

    settings = get_settings()
    if settings.auth_disabled:
        raise HTTPException(status_code=400, detail="Registration is disabled when AUTH_DISABLED=true")
    if not settings.jwt_secret:
        raise HTTPException(status_code=500, detail="JWT_SECRET must be set to register")

    async with get_db() as db:
        existing = await user_repo.get_by_email(db, body.email)
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")
        pw_hash = hash_password(body.password)
        user = await user_repo.create_user(db, body.name, body.email, pw_hash)

    token = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, user=user)


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginBody) -> TokenResponse:
    from backend.config import get_settings

    settings = get_settings()
    if settings.auth_disabled:
        raise HTTPException(status_code=400, detail="Use AUTH_DISABLED mode without login")
    if not settings.jwt_secret:
        raise HTTPException(status_code=500, detail="JWT_SECRET must be set")

    async with get_db() as db:
        row = await user_repo.get_by_email(db, body.email)
        if not row:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        user, pw_hash = row
        if not verify_password(body.password, pw_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, user=user)


@router.get("/auth/me", response_model=User)
async def me(user_id: str = Depends(get_current_user_id)) -> User:
    async with get_db() as db:
        u = await user_repo.get_by_id(db, user_id)
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        return u
