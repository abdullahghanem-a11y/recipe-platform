import io
import base64
import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import jwt
from app.core.database import get_db
from app.core.config import settings
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, hash_token,
    create_temp_token, decode_temp_token,
    generate_api_key, hash_api_key, get_current_user,
)
from app.models.user import User
from app.models.community import ApiKey
from app.schemas.user import (
    UserRegister, UserLogin, UserUpdate,
    PasswordChange, RefreshTokenRequest,
    RegisterOut, TokenOut, UserOut,
    TwoFactorVerify, TwoFactorValidate,
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Register ──────────────────────────────────────────────────────────

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
    user.refresh_token_hash = hash_token(refresh_token)
    await db.flush()

    return RegisterOut(
        user=UserOut.model_validate(user),
        access_token=access_token,
        refresh_token=refresh_token,
    )


# ── Login (2FA-aware) ─────────────────────────────────────────────────

@router.post("/login")
async def login(body: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # If 2FA is enabled and verified, require TOTP code before issuing full token
    if user.otp_enabled and user.otp_verified:
        temp_token = create_temp_token(user.id)
        return {
            "requires_2fa": True,
            "temp_token": temp_token,
        }

    # No 2FA — issue full token pair
    access_token = create_access_token({"sub": user.id})
    refresh_token = create_refresh_token({"sub": user.id})
    user.refresh_token_hash = hash_token(refresh_token)
    await db.flush()

    return TokenOut(
        access_token=access_token,
        refresh_token=refresh_token,
        requires_2fa=False,
    )


# ── Refresh ───────────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(body.refresh_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "refresh":
            raise credentials_exception
        user_id: str = payload.get("sub")
        if not user_id:
            raise credentials_exception
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token has expired, please log in again")
    except jwt.InvalidTokenError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise credentials_exception

    if not user.refresh_token_hash or user.refresh_token_hash != hash_token(body.refresh_token):
        raise credentials_exception

    new_access_token = create_access_token({"sub": user.id})
    new_refresh_token = create_refresh_token({"sub": user.id})
    user.refresh_token_hash = hash_token(new_refresh_token)
    await db.flush()

    return TokenOut(access_token=new_access_token, refresh_token=new_refresh_token)


# ── Logout ────────────────────────────────────────────────────────────

@router.post("/logout", status_code=204)
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.refresh_token_hash = None
    await db.flush()


# ── Me ────────────────────────────────────────────────────────────────

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


# ── Change password ───────────────────────────────────────────────────

@router.post("/change-password", status_code=204)
async def change_password(
    body: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    current_user.hashed_password = hash_password(body.new_password)
    current_user.refresh_token_hash = None
    await db.flush()


# ── API Keys ──────────────────────────────────────────────────────────

@router.post("/api-keys", status_code=201)
async def create_api_key(
    name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    raw_key = generate_api_key()
    key = ApiKey(key_hash=hash_api_key(raw_key), name=name)
    db.add(key)
    await db.flush()
    return {
        "key": raw_key,
        "name": name,
        "message": "Store this key securely — it will not be shown again.",
    }


# ── 2FA: Enable (generate secret + QR code) ──────────────────────────

@router.post("/2fa/enable")
async def enable_2fa(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a TOTP secret and return a QR code for the user to scan."""
    secret = pyotp.random_base32()

    # Store secret (not yet verified/enabled — user must verify first)
    current_user.otp_secret = secret
    current_user.otp_enabled = False
    current_user.otp_verified = False
    await db.flush()

    # Build provisioning URI
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(
        name=current_user.email,
        issuer_name="Reciply"
    )

    # Generate QR code as base64-encoded PNG
    qr = qrcode.make(uri)
    buffer = io.BytesIO()
    qr.save(buffer, format="PNG")
    qr_base64 = base64.b64encode(buffer.getvalue()).decode()

    return {
        "qr_code": qr_base64,
        "secret": secret,  # Show as text backup in case QR scan fails
        "message": "Scan the QR code with Google Authenticator, then call /auth/2fa/verify-setup with a valid code.",
    }


# ── 2FA: Verify setup ─────────────────────────────────────────────────

@router.post("/2fa/verify-setup")
async def verify_2fa_setup(
    body: TwoFactorVerify,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm the user's authenticator app is working by verifying a code."""
    if not current_user.otp_secret:
        raise HTTPException(status_code=400, detail="2FA setup not started. Call /auth/2fa/enable first.")

    totp = pyotp.TOTP(current_user.otp_secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(
            status_code=400,
            detail="Invalid code. Make sure your authenticator app is synced and try again."
        )

    # Mark 2FA as verified and active
    current_user.otp_enabled = True
    current_user.otp_verified = True
    await db.flush()

    return {"message": "2FA enabled successfully. You will need your authenticator app on every login."}


# ── 2FA: Validate login ───────────────────────────────────────────────

@router.post("/2fa/validate")
async def validate_2fa(
    body: TwoFactorValidate,
    db: AsyncSession = Depends(get_db),
):
    """Complete login by verifying the TOTP code after password check."""
    # Decode the temporary token issued after password check
    user_id = decode_temp_token(body.temp_token)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not user.otp_secret:
        raise HTTPException(status_code=400, detail="2FA not configured for this account")

    totp = pyotp.TOTP(user.otp_secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(
            status_code=401,
            detail="Invalid 2FA code. Check your authenticator app and try again."
        )

    # 2FA passed — issue full token pair
    access_token = create_access_token({"sub": user.id})
    refresh_token = create_refresh_token({"sub": user.id})
    user.refresh_token_hash = hash_token(refresh_token)
    await db.flush()

    return TokenOut(
        access_token=access_token,
        refresh_token=refresh_token,
    )


# ── 2FA: Disable ──────────────────────────────────────────────────────

@router.post("/2fa/disable")
async def disable_2fa(
    body: TwoFactorVerify,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable 2FA. Requires a valid TOTP code to confirm."""
    if not current_user.otp_enabled or not current_user.otp_secret:
        raise HTTPException(status_code=400, detail="2FA is not enabled on this account")

    totp = pyotp.TOTP(current_user.otp_secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(
            status_code=400,
            detail="Invalid code. Enter the current code from your authenticator app."
        )

    current_user.otp_secret = None
    current_user.otp_enabled = False
    current_user.otp_verified = False
    await db.flush()

    return {"message": "2FA has been disabled successfully."}