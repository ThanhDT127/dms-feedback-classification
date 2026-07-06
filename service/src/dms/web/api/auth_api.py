"""Authentication and user management API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import deps

router = APIRouter(prefix="/api/auth", tags=["auth"])
user_router = APIRouter(prefix="/api/users", tags=["users"])


# ---------- Auth Endpoints ----------


@router.post("/login")
async def login(payload: dict):
    """Authenticate user and return tokens."""
    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Vui lòng nhập tên đăng nhập và mật khẩu")

    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")

    user = store.authenticate(username, password)
    if user is None:
        raise HTTPException(status_code=401, detail="Tên đăng nhập hoặc mật khẩu không đúng")

    settings = deps.get_settings()
    from ...jwt_utils import create_token

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
    }


@router.post("/refresh")
async def refresh_token(payload: dict):
    """Refresh access token using refresh token."""
    token = payload.get("refresh_token", "")
    if not token:
        raise HTTPException(status_code=400, detail="Refresh token required")

    settings = deps.get_settings()
    if settings is None:
        raise HTTPException(status_code=500, detail="Settings not configured")

    from ...jwt_utils import create_token, decode_token
    import jwt as pyjwt

    try:
        token_payload = decode_token(token, settings.jwt_secret_key, expected_type="refresh")
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except (pyjwt.InvalidTokenError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

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
async def get_me(user: dict = Depends(deps.get_current_user)):
    """Return current user info."""
    return user


@router.post("/logout")
async def logout(user: dict = Depends(deps.get_current_user)):
    """Acknowledge logout for stateless JWT clients."""
    return {"success": True, "message": "Logged out"}


@router.post("/change-password")
async def change_password(payload: dict, user: dict = Depends(deps.get_current_user)):
    """Change the current user's password."""
    old_password = payload.get("old_password", "")
    new_password = payload.get("new_password", "")
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

    store.update_password(user["username"], new_password)
    return {"message": "Password updated successfully"}


# ---------- User Management Endpoints (admin only) ----------


@user_router.get("")
async def list_users(admin: dict = Depends(deps.get_admin_user)):
    """List all users (admin only)."""
    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")
    return {"users": store.list_users()}


@user_router.post("")
async def create_user(payload: dict, admin: dict = Depends(deps.get_admin_user)):
    """Create a new user (admin only)."""
    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    role = payload.get("role", "user")
    display_name = payload.get("display_name") or username
    is_active = payload.get("is_active", True)

    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
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
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@user_router.put("/{username}")
async def update_user(username: str, payload: dict, admin: dict = Depends(deps.get_admin_user)):
    """Update user fields (admin only)."""
    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")

    allowed = {"display_name", "role", "is_active", "password"}
    fields = {k: v for k, v in payload.items() if k in allowed}
    if not fields:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    if username == admin.get("username"):
        if fields.get("is_active") is False:
            raise HTTPException(status_code=400, detail="Cannot disable yourself")
        if fields.get("role") and fields.get("role") != "admin":
            raise HTTPException(status_code=400, detail="Cannot demote yourself")

    updated = store.update_user(username, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": updated}


@user_router.delete("/{username}")
async def delete_user(username: str, admin: dict = Depends(deps.get_admin_user)):
    """Delete user (admin only). Cannot delete yourself."""
    if username == admin.get("username"):
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    store = deps.get_user_store()
    if store is None:
        raise HTTPException(status_code=500, detail="User store not available")

    if not store.delete_user(username):
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": "User deleted"}
