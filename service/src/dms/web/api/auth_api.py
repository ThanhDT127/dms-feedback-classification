"""Authentication and user management API endpoints."""

from __future__ import annotations

import re

import jwt as pyjwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ...jwt_utils import create_token, decode_token
from ...token_blacklist import revoke
from .. import deps
from ..rate_limit import limiter

_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,50}$")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    username: str = Field("")
    password: str = Field("")


class RefreshTokenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    refresh_token: str = Field("")


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    old_password: str = Field("")
    new_password: str = Field("")


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    username: str = Field("")
    password: str = Field("")
    role: str = Field("user")
    display_name: str | None = None
    is_active: bool = True


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    username: str | None = None
    password: str | None = None
    role: str | None = None
    display_name: str | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    username: str
    display_name: str | None = None
    role: str
    is_active: bool = True
    must_change_password: bool = False
    created_at: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse
    must_change_password: bool = False


class RefreshTokenResponse(BaseModel):
    access_token: str


class ChangePasswordResponse(BaseModel):
    message: str
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UsersResponse(BaseModel):
    users: list[UserResponse]


class UserEnvelopeResponse(BaseModel):
    user: UserResponse


class MessageResponse(BaseModel):
    message: str


class LogoutResponse(BaseModel):
    success: bool
    message: str


def _validate_username(username: str) -> None:
    if not _USERNAME_PATTERN.match(username):
        raise HTTPException(
            status_code=400,
            detail="Username must be 3-50 characters, only letters, digits, underscore, dot, or hyphen.",
        )


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _revoke_access_token(token: str | None) -> None:
    if not token:
        return
    settings = deps.get_settings()
    if settings is None:
        return

    try:
        payload = decode_token(token, settings.jwt_secret_key, expected_type="access")
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError, ValueError):
        return

    jti = payload.get("jti")
    exp = payload.get("exp")
    if jti and exp:
        revoke(str(jti), float(exp))


router = APIRouter(prefix="/api/auth", tags=["auth"])
user_router = APIRouter(prefix="/api/users", tags=["users"])
CURRENT_USER_DEP = Depends(deps.get_current_user)
ADMIN_USER_DEP = Depends(deps.get_admin_user)
AUTHORIZATION_HEADER = Header(None)


# ---------- Auth Endpoints ----------


@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/minute")
async def login(request: Request, response: Response, payload: LoginRequest):
    """Authenticate user and return tokens."""
    username = payload.username.strip()
    password = payload.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="Vui lòng nhập tên đăng nhập và mật khẩu")

    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")

    user = store.authenticate(username, password)
    if user is None:
        raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu không đúng")

    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=500, detail="Cấu hình hệ thống không khả dụng")

    access_token = create_token(
        subject=username,
        token_type="access",
        secret_key=settings.jwt_secret_key,
        expires_minutes=30,
    )
    refresh_token = create_token(
        subject=username,
        token_type="refresh",
        secret_key=settings.jwt_secret_key,
        expires_minutes=60 * 24 * 7,  # 7 days
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": user,
        "must_change_password": user.get("must_change_password", False),
    }


@router.post("/refresh", response_model=RefreshTokenResponse)
@limiter.limit("10/minute")
async def refresh_token(request: Request, response: Response, payload: RefreshTokenRequest):
    """Refresh access token using refresh token."""
    token = payload.refresh_token
    if not token:
        raise HTTPException(status_code=400, detail="Refresh token required")

    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=500, detail="Settings not configured")

    try:
        token_payload = decode_token(token, settings.jwt_secret_key, expected_type="refresh")
    except pyjwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Refresh token expired") from exc
    except (pyjwt.InvalidTokenError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from exc

    username = token_payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")

    user = store.get_user(username)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    if user.get("is_active", True) is False:
        raise HTTPException(status_code=403, detail="User is inactive")

    new_access_token = create_token(
        subject=username,
        token_type="access",
        secret_key=settings.jwt_secret_key,
        expires_minutes=30,
    )

    return {"access_token": new_access_token}


@router.get("/me")
async def get_me(user: dict = CURRENT_USER_DEP) -> dict:
    """Return current user info."""
    return user


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    user: dict = CURRENT_USER_DEP,
    authorization: str | None = AUTHORIZATION_HEADER,
):
    """Revoke the current access token."""
    _revoke_access_token(_extract_bearer_token(authorization))
    return {"success": True, "message": "Logged out"}


@router.post("/change-password", response_model=ChangePasswordResponse)
@limiter.limit("3/minute")
async def change_password(
    request: Request,
    response: Response,
    payload: ChangePasswordRequest,
    user: dict = CURRENT_USER_DEP,
    authorization: str | None = AUTHORIZATION_HEADER,
):
    """Change the current user's password."""
    old_password = payload.old_password
    new_password = payload.new_password
    if not old_password or not new_password:
        raise HTTPException(status_code=400, detail="old_password and new_password required")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")

    # Re-verify the old password to confirm identity.
    verified = store.authenticate(user["username"], old_password)
    if verified is None:
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    if not store.update_password(user["username"], new_password):
        raise HTTPException(status_code=404, detail="User not found")

    _revoke_access_token(_extract_bearer_token(authorization))

    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=500, detail="Settings not configured")

    username = user["username"]
    access_token = create_token(
        subject=username,
        token_type="access",
        secret_key=settings.jwt_secret_key,
        expires_minutes=30,
    )
    refresh_token = create_token(
        subject=username,
        token_type="refresh",
        secret_key=settings.jwt_secret_key,
        expires_minutes=60 * 24 * 7,
    )
    return {
        "message": "Password updated successfully",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


# ---------- User Management Endpoints (admin only) ----------


@user_router.get("", response_model=UsersResponse)
async def list_users(admin: dict = ADMIN_USER_DEP):
    """List all users (admin only)."""
    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")
    return {"users": store.list_users()}


@user_router.post("", response_model=UserEnvelopeResponse)
async def create_user(payload: UserCreateRequest, admin: dict = ADMIN_USER_DEP):
    """Create a new user (admin only)."""
    username = payload.username.strip()
    password = payload.password
    role = payload.role
    display_name = payload.display_name or username
    is_active = payload.is_active

    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
    _validate_username(username)
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="role must be 'admin' or 'user'")

    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")

    try:
        user = store.create_user(
            username,
            password,
            role,
            display_name=display_name,
            is_active=bool(is_active),
        )
        return {"user": user}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@user_router.put("/{username}", response_model=UserEnvelopeResponse)
async def update_user(username: str, payload: UserUpdateRequest, admin: dict = ADMIN_USER_DEP):
    """Update user fields (admin only)."""
    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")

    allowed = {"display_name", "role", "is_active", "password", "username"}
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if k in allowed}
    if not fields:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    # Validate new username if it is being changed
    new_username = fields.get("username")
    if new_username and new_username != username:
        _validate_username(new_username)
    if username == admin.get("username"):
        if fields.get("is_active") is False:
            raise HTTPException(status_code=400, detail="Cannot disable yourself")
        if fields.get("role") and fields.get("role") != "admin":
            raise HTTPException(status_code=400, detail="Cannot demote yourself")

    updated = store.update_user(username, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": updated}


@user_router.delete("/{username}", response_model=MessageResponse)
async def delete_user(username: str, admin: dict = ADMIN_USER_DEP):
    """Delete user (admin only). Cannot delete yourself."""
    if username == admin.get("username"):
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")

    if not store.delete_user(username):
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted"}
