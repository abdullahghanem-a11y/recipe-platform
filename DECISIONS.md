# DECISIONS.md — AI-Powered Recipe Sharing Platform

**Course:** SFWE477 — Backend Development & DevOps Fundamentals  
**Student:** Abdullah  
**Project:** Reciply — AI-Powered Recipe Sharing Platform  
**Live URL:** https://reciply.up.railway.app  
**Backend API:** https://recipe-platform-production-e21c.up.railway.app  

---

## Table of Contents

1. [Authentication Strategy](#1-authentication-strategy)
2. [Two-Factor Authentication (2FA)](#2-two-factor-authentication-2fa)
3. [Token Storage & Security](#3-token-storage--security)
4. [AI Model Architecture](#4-ai-model-architecture)
5. [Database Design](#5-database-design)
6. [API Key System](#6-api-key-system)
7. [Rate Limiting](#7-rate-limiting)
8. [Deployment](#8-deployment)

---

## 1. Authentication Strategy

**Decision:** JWT (JSON Web Tokens) with access + refresh token rotation.

**Why JWT over sessions:**
- Stateless — the server does not need to store session data. Each token is self-contained and verifiable using the shared secret key.
- Scales horizontally without a shared session store.
- Industry standard for REST APIs consumed by SPAs and mobile apps.

**Token lifetimes:**
- Access token: 30 minutes — short enough to limit damage if stolen.
- Refresh token: 7 days — stored as a SHA-256 hash in the database, rotated on every use (refresh token rotation). If a refresh token is stolen and used, the legitimate user's next refresh will fail and they will be logged out.

**Refresh token rotation:** When a refresh token is used, a new access + refresh pair is issued and the old refresh token hash is replaced. This means a stolen refresh token can only be used once before it is invalidated.

---

## 2. Two-Factor Authentication (2FA)

### What We Implemented

TOTP-based (Time-Based One-Time Password) 2FA using Google Authenticator or any compatible authenticator app (Authy, Microsoft Authenticator). Implemented using the `pyotp` library.

### How TOTP Works

1. When a user enables 2FA, the server generates a random Base32 secret key using `pyotp.random_base32()` and stores it in the user's database record.
2. The server builds a provisioning URI in the format `otpauth://totp/Reciply:user@email.com?secret=SECRET&issuer=Reciply` and renders it as a QR code using the `qrcode` library.
3. The user scans the QR code with Google Authenticator. The app stores the secret locally on their phone.
4. Every 30 seconds, both the authenticator app and our server independently compute the same 6-digit code using HMAC-SHA1 applied to the shared secret and the current Unix timestamp (divided into 30-second windows).
5. The server verifies the code the user submits by computing what the code should be right now and comparing. No communication with the phone is needed — the math produces the same result on both sides.

**Interview-ready explanation:** "TOTP works because both the server and the authenticator app share the same secret key. They both use that key plus the current time to independently generate the same code. The server never needs to communicate with the app — it just computes what the code should be and checks if they match."

### Why TOTP Over SMS-Based OTP

| Factor | TOTP | SMS OTP |
|--------|------|---------|
| Cost | Free — no carrier fees | Paid per SMS (Twilio, etc.) |
| SIM-swap attacks | Not vulnerable | Vulnerable — attacker ports victim's number |
| Phone number required | No | Yes — privacy concern |
| Works offline | Yes — no network needed | No — requires mobile signal |
| Industry standard | GitHub, Google, AWS, Stripe | Deprecated by NIST (SP 800-63B) |
| Setup complexity | One QR scan | Requires SMS provider integration |

NIST Special Publication 800-63B (2017) explicitly recommends against SMS-based OTP due to SIM-swapping and SS7 protocol vulnerabilities. TOTP is the correct choice for any production system.

### How otp_secret Is Stored

The `otp_secret` is stored as a plaintext Base32 string in the `users` table.

**Tradeoff acknowledged:** In a fully hardened production system, the secret should be encrypted at rest using a key stored in a secrets manager (AWS KMS, HashiCorp Vault). We did not implement this due to time constraints and the added complexity of key management infrastructure.

**Why it is still acceptable for this project:**
- The secret alone is not enough to log in — an attacker also needs the user's password.
- The database itself is protected by Railway's infrastructure and is not publicly accessible.
- Encrypting the secret would require decrypting it on every login, adding latency and key management complexity.

**Production recommendation:** Encrypt `otp_secret` using AES-256-GCM with a key stored in AWS KMS or equivalent. Rotate the encryption key periodically.

### What Happens If a User Loses Their Phone

Currently, there is no automated recovery mechanism. This is a known limitation.

**What we would implement in production:**
1. **Recovery codes:** At 2FA setup time, generate 8–10 single-use recovery codes (random strings, stored as bcrypt hashes). The user saves these offline. Any recovery code can be used once instead of a TOTP code.
2. **Admin bypass:** A support flow where the user verifies their identity through email + government ID, and an admin manually disables 2FA on their account.
3. **Trusted devices:** After login, offer "trust this device for 30 days" which sets a long-lived httpOnly cookie, bypassing 2FA on that device.

### What valid_window=1 Means

```python
totp.verify(code, valid_window=1)
```

This accepts TOTP codes from the **previous**, **current**, and **next** 30-second window — a total of a 90-second acceptance window.

**Why it is necessary:** User clocks are not perfectly synchronized with the server clock. A phone that is a few seconds behind might generate a code from the "previous" window. Without `valid_window=1`, legitimate users would frequently get "invalid code" errors due to clock drift. The ±30 second tolerance is the industry standard balance between security and usability.

### Login Flow With 2FA

```
POST /auth/login (email + password)
    ↓
Password valid + 2FA enabled?
    ├── No 2FA → issue access_token + refresh_token → done
    └── Yes 2FA → issue temp_token (type="2fa_pending", 5min TTL)
                      ↓
              POST /auth/2fa/validate (temp_token + TOTP code)
                      ↓
              Code valid → issue access_token + refresh_token → done
```

The `temp_token` has `type="2fa_pending"` in its JWT payload. The `get_current_user` dependency rejects any token where `type != "access"`, so the temp token cannot be used to access protected endpoints — only to complete the 2FA step.

### Endpoints Implemented

| Endpoint | Description |
|----------|-------------|
| `POST /auth/2fa/enable` | Generates secret, returns QR code as base64 PNG |
| `POST /auth/2fa/verify-setup` | Verifies first code, activates 2FA |
| `POST /auth/2fa/validate` | Exchanges temp_token + TOTP code for full tokens |
| `POST /auth/2fa/disable` | Disables 2FA after verifying current code |
| `POST /auth/login` | Modified — returns `requires_2fa: true` + `temp_token` if 2FA active |

---

## 3. Token Storage & Security

**Decision:** Tokens stored in browser cookies with `secure` and `sameSite: strict` flags. API key stored in memory only (never persisted).

**Why not localStorage:**
- localStorage is accessible by any JavaScript running on the page, making it vulnerable to XSS (Cross-Site Scripting) attacks. If an attacker injects a script, they can steal all tokens from localStorage.
- Cookies with the `secure` flag are only sent over HTTPS, preventing interception over HTTP.
- `sameSite: strict` prevents the cookie from being sent in cross-site requests, mitigating CSRF attacks.

**Why not httpOnly cookies:**
- httpOnly cookies cannot be read by JavaScript at all — they are the most secure option.
- However, setting httpOnly cookies requires the server to set them via `Set-Cookie` response headers, which requires a different architecture (session-based or BFF pattern).
- Our current architecture uses a JWT-based SPA where the client manages tokens. Migrating to httpOnly cookies would require backend changes to set/clear cookies directly.

**Production recommendation:** Move to httpOnly cookies set by the backend on login/logout, eliminating JavaScript-accessible token storage entirely.

**API key in memory:** The API key used for AI endpoints is stored in JavaScript memory only and re-fetched from the server on page load. It is never written to localStorage, sessionStorage, or cookies. This means it is lost on page refresh but is immediately re-fetched, which is an acceptable tradeoff for security.

---

## 4. AI Model Architecture

**Decision:** Hybrid approach — lightweight models run locally on CPU, large models use Hugging Face Hosted Inference API.

| Endpoint | Model | Runs Where | Why |
|----------|-------|------------|-----|
| `/ai/recognize` | `google/vit-base-patch16-224` | Local CPU | 86M params, fast, no GPU needed |
| `/ai/generate` | `Qwen/Qwen2.5-7B-Instruct` | HF API | 7B params — too large for CPU |
| `/ai/nutrition` | `Qwen/Qwen2.5-7B-Instruct` | HF API | Requires reasoning capability |
| `/ai/substitute` | `Qwen/Qwen2.5-7B-Instruct` | HF API | Requires domain knowledge |
| `/ai/assist` | Both | Hybrid | ViT for recognition + Qwen for generation |

**Why Qwen2.5-7B over GPT-4/Claude:**
- Free tier available on Hugging Face — no per-token cost.
- Sufficient quality for recipe generation tasks.
- No vendor lock-in.

**Tradeoff:** HF Inference API response times are 5–15 seconds. A production system would use a dedicated GPU instance for sub-second responses.

---

## 5. Database Design

**Decision:** PostgreSQL with SQLAlchemy async ORM. No full Alembic migration tracking for column additions — raw SQL `ALTER TABLE IF NOT EXISTS` in `main.py` startup instead.

**Why raw SQL over Alembic for new columns:**
- Alembic requires generating a migration file, committing it, and ensuring it runs in order. For simple `ADD COLUMN` operations, this adds friction without meaningful benefit.
- `ALTER TABLE users ADD COLUMN IF NOT EXISTS` is idempotent — safe to run on every startup without side effects.
- Alembic is still used for the initial schema creation via `upgrade head`.

**Why async SQLAlchemy:**
- FastAPI is an async framework. Using sync SQLAlchemy would block the event loop on every database query, eliminating the performance benefit of async.
- `asyncpg` driver provides the best PostgreSQL async performance in Python.

---

## 6. API Key System

**Decision:** Separate API key authentication for AI endpoints, distinct from JWT auth for community endpoints.

**Why separate auth for AI endpoints:**
- AI endpoints are expensive (compute + HF API calls). A separate key allows per-key rate limiting without affecting the user's JWT session.
- Allows future monetization — different tiers with different rate limits per API key.
- Keys are stored as SHA-256 hashes in the database — the raw key is shown once at creation and never stored in plaintext.

---

## 7. Rate Limiting

**Decision:** Redis-based rate limiting per API key per endpoint per minute.

| Endpoint | Limit/min | Reason |
|----------|-----------|--------|
| `/ai/recognize` | 30 | Lightweight model, higher limit |
| `/ai/generate` | 10 | Expensive HF API call |
| `/ai/nutrition` | 30 | Moderate cost |
| `/ai/substitute` | 30 | Moderate cost |
| `/ai/assist` | 5 | Most expensive — calls multiple models |

**Implementation:** Redis `INCR` + `EXPIRE` pattern. Each key maps to `rate_limit:{endpoint}:{api_key_id}:{minute_window}`. Atomic increment ensures accuracy under concurrent requests.

---

## 8. Deployment

**Decision:** Railway for both backend and frontend. Docker for local development.

**Why Railway over Render/Heroku:**
- Persistent PostgreSQL and Redis included with no cold starts.
- Docker-based deployment — same environment locally and in production.
- Free tier sufficient for a portfolio project.
- Automatic deploys on `git push`.

**Frontend:** Next.js 14 deployed on Railway. Domain: `reciply.up.railway.app`.

**Backend:** FastAPI in Docker on Railway. Startup runs Alembic migrations + raw SQL column additions automatically on every deploy, ensuring the production database is always in sync with the codebase.
