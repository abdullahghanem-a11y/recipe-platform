from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional


class Ingredient(BaseModel):
    name: str
    quantity: str
    unit: str = ""

    @field_validator("name", "quantity", "unit")
    @classmethod
    def strip_whitespace(cls, v):
        return v.strip()

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if len(v.strip()) == 0:
            raise ValueError("Ingredient name cannot be empty")
        if len(v) > 100:
            raise ValueError("Ingredient name cannot exceed 100 characters")
        return v.strip()


class RecipeCreate(BaseModel):
    title: str
    description: str = ""
    ingredients: list[Ingredient] = []
    steps: list[str] = []
    cuisine_type: str = ""
    prep_time_minutes: int = 0
    cook_time_minutes: int = 0
    servings: int = 1

    @field_validator("title")
    @classmethod
    def validate_title(cls, v):
        v = v.strip()
        if len(v) == 0:
            raise ValueError("Title cannot be empty")
        if len(v) > 200:
            raise ValueError("Title cannot exceed 200 characters")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v):
        if len(v) > 2000:
            raise ValueError("Description cannot exceed 2000 characters")
        return v.strip()

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v):
        if len(v) > 30:
            raise ValueError("Recipe cannot have more than 30 steps")
        cleaned = [s.strip() for s in v if s.strip()]
        if len(cleaned) == 0 and len(v) > 0:
            raise ValueError("Steps cannot be empty strings")
        return cleaned

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, v):
        if len(v) > 20:
            raise ValueError("Recipe cannot have more than 20 ingredients")
        return v

    @field_validator("prep_time_minutes", "cook_time_minutes")
    @classmethod
    def validate_time(cls, v):
        if v < 0:
            raise ValueError("Time cannot be negative")
        if v > 10080:
            raise ValueError("Time value is unrealistically large")
        return v

    @field_validator("servings")
    @classmethod
    def validate_servings(cls, v):
        if v < 1:
            raise ValueError("Servings must be at least 1")
        if v > 1000:
            raise ValueError("Servings value is unrealistically large")
        return v


class RecipeUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    ingredients: Optional[list[Ingredient]] = None
    steps: Optional[list[str]] = None
    cuisine_type: Optional[str] = None
    prep_time_minutes: Optional[int] = None
    cook_time_minutes: Optional[int] = None
    servings: Optional[int] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v):
        if v is not None:
            v = v.strip()
            if len(v) == 0:
                raise ValueError("Title cannot be empty")
            if len(v) > 200:
                raise ValueError("Title cannot exceed 200 characters")
        return v

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v):
        if v is not None and len(v) > 30:
            raise ValueError("Recipe cannot have more than 30 steps")
        return v

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, v):
        if v is not None and len(v) > 20:
            raise ValueError("Recipe cannot have more than 20 ingredients")
        return v


class RecipeOut(BaseModel):
    id: str
    user_id: str
    title: str
    description: str
    ingredients: list
    steps: list
    cuisine_type: str
    prep_time_minutes: int
    cook_time_minutes: int
    servings: int
    average_rating: Optional[float] = None
    comment_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class RecipeListOut(BaseModel):
    results: list[RecipeOut]
    total: int
    page: int
    limit: int


class RatingCreate(BaseModel):
    score: int

    @field_validator("score")
    @classmethod
    def validate_score(cls, v):
        if v < 1 or v > 5:
            raise ValueError("Score must be between 1 and 5")
        return v


class RatingOut(BaseModel):
    recipe_id: str
    user_id: str
    score: int
    recipe_average_rating: Optional[float]


class SaveRecipeRequest(BaseModel):
    collection_id: Optional[str] = None


class CollectionCreate(BaseModel):
    name: str


class CollectionOut(BaseModel):
    id: str
    name: str
    user_id: str
    recipe_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


# ── comment schemas ───────────────────────────────────────────────────

class CommentCreate(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def validate_body(cls, v):
        v = v.strip()
        if len(v) == 0:
            raise ValueError("Comment cannot be empty")
        if len(v) > 1000:
            raise ValueError("Comment cannot exceed 1000 characters")
        return v


class CommentUpdate(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def validate_body(cls, v):
        v = v.strip()
        if len(v) == 0:
            raise ValueError("Comment cannot be empty")
        if len(v) > 1000:
            raise ValueError("Comment cannot exceed 1000 characters")
        return v


class CommentOut(BaseModel):
    id: str
    recipe_id: str
    user_id: str
    username: str
    body: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CommentListOut(BaseModel):
    results: list[CommentOut]
    total: int
    page: int
    limit: int
