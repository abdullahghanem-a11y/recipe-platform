import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    dietary_preferences: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    recipes = relationship("Recipe", back_populates="author", cascade="all, delete")
    ratings = relationship("Rating", back_populates="user", cascade="all, delete")
    saved_recipes = relationship("SavedRecipe", back_populates="user", cascade="all, delete")
    collections = relationship("Collection", back_populates="user", cascade="all, delete")
    inference_logs = relationship("InferenceLog", back_populates="user", cascade="all, delete")