# Recipe Platform API

AI-Powered Recipe Sharing Platform — FastAPI + PostgreSQL + Redis + Hugging Face

---

## Quick start (local)

```bash
# 1. Clone the repo
git clone https://github.com/your-username/recipe-platform-api.git
cd recipe-platform-api

# 2. Copy and fill in environment variables
cp .env.example .env
# Edit .env — set SECRET_KEY and HUGGINGFACE_API_KEY

# 3. Start the full stack
docker-compose up --build

# 4. Visit the API docs
open http://localhost:8000/docs
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL async connection string |
| `SECRET_KEY` | yes | JWT signing secret — use a long random string |
| `REDIS_URL` | no | Redis URL (default: `redis://localhost:6379`) |
| `HUGGINGFACE_API_KEY` | yes | HF Inference API key for text generation |
| `POSTGRES_PASSWORD` | no | Postgres password for Docker Compose (default: `password`) |

Generate a secure `SECRET_KEY`:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Architecture

```
FastAPI (port 8000)
├── Community layer      JWT auth   → PostgreSQL
│   ├── POST /auth/register
│   ├── POST /auth/login
│   ├── GET/PATCH /users/me
│   ├── GET /users/me/saved
│   ├── POST/GET/PATCH/DELETE /recipes
│   ├── POST /recipes/{id}/ratings
│   ├── POST /recipes/{id}/save
│   └── POST/GET /collections
│
└── AI layer             API key auth → HF models + Redis cache
    ├── POST /ai/recognize    (ViT — local CPU)
    ├── POST /ai/generate     (Zephyr-7b — HF hosted API)
    ├── POST /ai/nutrition    (Mistral — HF hosted API)
    ├── POST /ai/substitute   (CLIP — local CPU + Mistral)
    └── POST /ai/assist       (unified pipeline)
```

### Hybrid model architecture

| Model | Runs | Why |
|---|---|---|
| `google/vit-base-patch16-224` | Local CPU | Fast, lightweight (86M params) |
| `openai/clip-vit-base-patch32` | Local CPU | Fast, no GPU required |
| `HuggingFaceH4/zephyr-7b-beta` | HF hosted API | 7B params — too large for CPU |
| `mistralai/Mistral-7B-Instruct-v0.1` | HF hosted API | 7B params — too large for CPU |

---

## API authentication

**Community endpoints** — JWT Bearer token:
```
Authorization: Bearer <token>
```

**AI endpoints** — API key:
```
X-API-Key: <your-api-key>
```

Generate an API key (requires JWT):
```bash
curl -X POST "http://localhost:8000/auth/api-keys?name=my-app" \
  -H "Authorization: Bearer <jwt-token>"
```

---

## Rate limits (per API key, per minute)

| Endpoint | Limit |
|---|---|
| `/ai/recognize` | 30 |
| `/ai/generate` | 10 |
| `/ai/nutrition` | 30 |
| `/ai/substitute` | 30 |
| `/ai/assist` | 5 |

---

## Deploy to Render

1. Push your repo to GitHub
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Set these values:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Add environment variables in the Render dashboard:
   - `DATABASE_URL` — use Render's PostgreSQL add-on
   - `REDIS_URL` — use Render's Redis add-on
   - `SECRET_KEY` — generate with the command above
   - `HUGGINGFACE_API_KEY` — from huggingface.co/settings/tokens

### Expected response times on Render free tier

| Endpoint | Cold (first request) | Warm |
|---|---|---|
| `/health` | < 500ms | < 50ms |
| `/auth/register` | < 1s | < 200ms |
| `/recipes` (list) | < 1s | < 300ms |
| `/ai/recognize` | 10–30s (model load) | 400–600ms |
| `/ai/generate` | 5–15s (HF API) | 5–15s |
| `/ai/nutrition` | 5–15s (HF API) | 5–15s |
| `/ai/substitute` | 10–30s (model load) | 500–800ms |
| `/ai/assist` | 15–45s | 8–20s |

> AI endpoints are slow on first request because local models load into memory. Subsequent requests within the same server instance are significantly faster.

---

## Project structure

```
app/
├── core/
│   ├── config.py       # environment settings
│   ├── database.py     # SQLAlchemy async engine
│   ├── redis.py        # cache + rate limiting
│   └── security.py     # JWT + API key auth
├── models/
│   ├── user.py         # users table
│   ├── recipe.py       # recipes table
│   └── community.py    # ratings, collections, saved_recipes, inference_logs, api_keys
├── schemas/
│   ├── user.py         # auth request/response shapes
│   └── recipe.py       # recipe request/response shapes
├── routers/
│   ├── auth.py         # register, login, api-keys
│   ├── users.py        # profile, saved recipes
│   ├── recipes.py      # CRUD, ratings, save
│   ├── collections.py  # collections
│   └── ai.py           # 5 AI endpoints
├── services/
│   └── ai.py           # model loading + inference logic
└── main.py             # app entry point
```