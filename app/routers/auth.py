from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import jwt
from app.core.database import get_db
from app.core.config import settings
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, hash_token,
    generate_api_key, hash_api_key, get_current_user,
)
from app.models.user import User
from app.models.community import ApiKey
from app.schemas.user import (
    UserRegister, UserLogin, UserUpdate,
    PasswordChange, RefreshTokenRequest,
    RegisterOut, TokenOut, UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterOut, status_code=201)
async def register(body: UserRegister, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(User).where(
            (User.email == body.email) | (User.username == body.username)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email or username already taken")

    user = User(
        username=body.username,
        email=body.email,
        hashed_password=hash_password(body.password),
        dietary_preferences=body.dietary_preferences,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    access_token = create_access_token({"sub": user.id})
    refresh_token = create_refresh_token({"sub": user.id})

    # Store hashed refresh token
    user.refresh_token_hash = hash_token(refresh_token)
    await db.flush()

    return RegisterOut(
        user=UserOut.model_validate(user),
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/login", response_model=TokenOut)
async def login(body: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token({"sub": user.id})
    refresh_token = create_refresh_token({"sub": user.id})

    # Rotate: store new refresh token hash, invalidating the old one
    user.refresh_token_hash = hash_token(refresh_token)
    await db.flush()

    return TokenOut(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            body.refresh_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        if payload.get("type") != "refresh":
            raise credentials_exception
        user_id: str = payload.get("sub")
        if not user_id:
            raise credentials_exception
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired, please log in again",
        )
    except jwt.InvalidTokenError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise credentials_exception

    # Validate token matches what we stored (prevents reuse of old tokens)
    if not user.refresh_token_hash or user.refresh_token_hash != hash_token(body.refresh_token):
        raise credentials_exception

    # Issue new token pair (rotation — old refresh token is now invalid)
    new_access_token = create_access_token({"sub": user.id})
    new_refresh_token = create_refresh_token({"sub": user.id})
    user.refresh_token_hash = hash_token(new_refresh_token)
    await db.flush()

    return TokenOut(access_token=new_access_token, refresh_token=new_refresh_token)


@router.post("/logout", status_code=204)
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Invalidate the refresh token. The access token expires naturally."""
    current_user.refresh_token_hash = None
    await db.flush()


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    body: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.username is not None:
        # Check username not taken by someone else
        result = await db.execute(
            select(User).where(User.username == body.username, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Username already taken")
        current_user.username = body.username

    if body.dietary_preferences is not None:
        current_user.dietary_preferences = body.dietary_preferences

    await db.flush()
    await db.refresh(current_user)
    return UserOut.model_validate(current_user)


@router.post("/change-password", status_code=204)
async def change_password(
    body: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    current_user.hashed_password = hash_password(body.new_password)
    # Invalidate all refresh tokens on password change
    current_user.refresh_token_hash = None
    await db.flush()


@router.post("/api-keys", status_code=201)
async def create_api_key(
    name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    raw_key = generate_api_key()
    key = ApiKey(
        key_hash=hash_api_key(raw_key),
        name=name,
    )
    db.add(key)
    await db.flush()

    return {
        "key": raw_key,
        "name": name,
        "message": "Store this key securely — it will not be shown again.",
    }