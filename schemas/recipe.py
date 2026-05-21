from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional


class Ingredient(BaseModel):
    name: str
    quantity: str
    unit: str = ""


class RecipeCreate(BaseModel):
    title: str
    description: str = ""
    ingredients: list[Ingredient] = []
    steps: list[str] = []
    cuisine_type: str = ""
    prep_time_minutes: int = 0
    cook_time_minutes: int = 0
    servings: int = 1

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v):
        if len(v) > 30:
            raise ValueError("Recipe cannot have more than 30 steps")
        return v

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, v):
        if len(v) > 20:
            raise ValueError("Recipe cannot have more than 20 ingredients")
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