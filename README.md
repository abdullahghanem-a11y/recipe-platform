# Reciply — AI-Powered Recipe Sharing Platform

> **Live:** https://reciply.up.railway.app  
> **API:** https://recipe-platform-production-e21c.up.railway.app  
> **API Docs:** https://recipe-platform-production-e21c.up.railway.app/docs

Full-stack AI-powered recipe platform built with FastAPI, Next.js 14, PostgreSQL, Redis, and Hugging Face models.

---

## Features

### Community
- Browse, search, and filter recipes by cuisine, dietary preference, time, and rating
- Create, edit, and delete your own recipes
- Rate and comment on recipes
- Save recipes to personal collections
- User profiles with dietary preferences

### AI
- **Recipe Generation** — describe ingredients and get a full recipe
- **Nutrition Analysis** — AI-estimated per-serving nutrition for any recipe
- **Ingredient Substitutions** — find alternatives with similarity scores and effect descriptions
- **Dish Recognition** — upload a food photo and identify the dish
- **Smart Assistant** — combine image + ingredients for recipe + nutrition + substitutions in one call

### Security
- JWT authentication with refresh token rotation
- **Two-Factor Authentication (2FA)** — TOTP via Google Authenticator
- API key authentication for AI endpoints
- Redis-based rate limiting per endpoint
- Secure cookie storage (`secure` + `sameSite: strict`)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI, Python 3.11 |
| Database | PostgreSQL (async via asyncpg) |
| ORM | SQLAlchemy async + Alembic |
| Cache / Rate limiting | Redis |
| AI — text | Qwen/Qwen2.5-7B-Instruct (Hugging Face API) |
| AI — vision | google/vit-base-patch16-224 (local CPU) |
| 2FA | pyotp (TOTP), qrcode |
| Frontend | Next.js 14, TypeScript, Zustand |
| Deployment | Railway (backend + frontend) |
| Local dev | Docker Compose |

---

## Quick Start (Local)

```bash
# 1. Clone the repo
git clone https://github.com/your-username/recipe-platform.git
cd recipe-platform

# 2. Copy and fill in environment variables
cp .env.example .env
# Edit .env — set SECRET_KEY and HUGGINGFACE_API_KEY

# 3. Start the full stack
docker-compose up --build

# 4. Visit the API docs
open http://localhost:8000/docs
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | yes | PostgreSQL async connection string |
| `SECRET_KEY` | yes | JWT signing secret — use a long random string |
| `REDIS_URL` | no | Redis URL (default: `redis://localhost:6379`) |
| `HUGGINGFACE_API_KEY` | yes | HF Inference API key for text generation |
| `POSTGRES_PASSWORD` | no | Postgres password for Docker Compose |

Generate a secure `SECRET_KEY`:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## API Authentication

**Community endpoints** — JWT Bearer token:
```
Authorization: Bearer <access_token>
```

**AI endpoints** — API key:
```
X-API-Key: <your-api-key>
```

Generate an API key (requires JWT):
```bash
curl -X POST "https://recipe-platform-production-e21c.up.railway.app/auth/api-keys?name=my-app" \
  -H "Authorization: Bearer <jwt-token>"
```

---

## Two-Factor Authentication

2FA uses TOTP (Time-Based One-Time Password) compatible with Google Authenticator, Authy, and Microsoft Authenticator.

**Setup flow:**
1. Log in → click 🔒 Security in the navbar
2. Click **Enable 2FA** → scan the QR code with Google Authenticator
3. Enter the 6-digit code to verify → 2FA is now active

**Login flow with 2FA:**
1. Enter email + password → server returns `requires_2fa: true` + a short-lived `temp_token`
2. Enter the 6-digit code from your authenticator app
3. Server issues full access + refresh tokens

See [DECISIONS.md](./DECISIONS.md#2-two-factor-authentication-2fa) for full technical details.

---

## API Endpoints

### Auth
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/register` | Register new user |
| POST | `/auth/login` | Login (returns `requires_2fa` flag if 2FA enabled) |
| POST | `/auth/refresh` | Refresh access token |
| POST | `/auth/logout` | Logout (invalidates refresh token) |
| GET | `/auth/me` | Get current user |
| PATCH | `/auth/me` | Update username / dietary preferences |
| POST | `/auth/change-password` | Change password |
| POST | `/auth/2fa/enable` | Start 2FA setup (returns QR code) |
| POST | `/auth/2fa/verify-setup` | Confirm 2FA setup with first code |
| POST | `/auth/2fa/validate` | Complete 2FA login |
| POST | `/auth/2fa/disable` | Disable 2FA |

### Recipes
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/recipes` | List recipes (search, filter, sort, paginate) |
| POST | `/recipes` | Create recipe |
| GET | `/recipes/{id}` | Get recipe detail |
| PATCH | `/recipes/{id}` | Update recipe (owner only) |
| DELETE | `/recipes/{id}` | Delete recipe (owner only) |
| POST | `/recipes/{id}/ratings` | Rate a recipe |
| POST | `/recipes/{id}/comments` | Add a comment |
| DELETE | `/recipes/{id}/comments/{comment_id}` | Delete a comment |
| POST | `/recipes/{id}/save` | Save to collection |

### Collections
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/collections` | List user's collections |
| POST | `/collections` | Create collection |
| DELETE | `/collections/{id}` | Delete collection |

### AI
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/ai/generate` | Generate recipe from ingredients |
| POST | `/ai/nutrition` | Analyze nutrition for ingredients |
| POST | `/ai/substitute` | Find ingredient substitutions |
| POST | `/ai/recognize` | Identify dish from image |
| POST | `/ai/assist` | Full assistant pipeline |

---

## Rate Limits (per API key, per minute)

| Endpoint | Limit |
|----------|-------|
| `/ai/recognize` | 30 |
| `/ai/generate` | 10 |
| `/ai/nutrition` | 30 |
| `/ai/substitute` | 30 |
| `/ai/assist` | 5 |

---

## Project Structure

```
app/
├── core/
│   ├── config.py        # environment settings
│   ├── database.py      # SQLAlchemy async engine
│   ├── redis.py         # cache + rate limiting
│   └── security.py      # JWT, API key auth, 2FA token helpers
├── models/
│   ├── user.py          # users table (incl. otp_secret, otp_enabled, otp_verified)
│   ├── recipe.py        # recipes table
│   └── community.py     # ratings, comments, collections, saved_recipes, api_keys
├── schemas/
│   ├── user.py          # auth + 2FA request/response schemas
│   └── recipe.py        # recipe schemas
├── routers/
│   ├── auth.py          # auth + 2FA endpoints
│   ├── users.py         # profile, saved recipes
│   ├── recipes.py       # CRUD, ratings, comments, save
│   ├── collections.py   # collections CRUD
│   └── ai.py            # 5 AI endpoints
├── services/
│   └── ai.py            # model loading + inference logic
└── main.py              # app entry point + startup migrations
```

---

## Architecture Decisions

See [DECISIONS.md](./DECISIONS.md) for detailed reasoning behind:
- JWT + refresh token rotation strategy
- TOTP 2FA implementation and security tradeoffs
- Token storage security (cookies vs localStorage)
- AI model selection (local vs hosted)
- Database migration strategy
- Rate limiting implementation