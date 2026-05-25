from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.core.database import engine
from sqlalchemy import text


async def run_migrations():
    """Run Alembic migrations + raw SQL column additions on startup."""
    import subprocess
    result = subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Alembic migration failed:\n{result.stderr}")
    if result.stdout:
        print(result.stdout)

    # 2FA columns — safe to run every startup (IF NOT EXISTS)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS otp_secret VARCHAR"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS otp_enabled BOOLEAN DEFAULT false"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS otp_verified BOOLEAN DEFAULT false"
        ))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_migrations()
    yield
    await engine.dispose()


app = FastAPI(
    title="Recipe Platform API",
    description="AI-Powered Recipe Sharing Platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import auth, users, recipes, collections, ai  # noqa: E402

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(recipes.router)
app.include_router(collections.router)
app.include_router(ai.router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}