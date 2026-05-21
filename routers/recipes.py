from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.recipe import Recipe
from app.models.community import Rating, SavedRecipe
from app.schemas.recipe import (
    RecipeCreate, RecipeUpdate, RecipeOut, RecipeListOut,
    RatingCreate, RatingOut, SaveRecipeRequest,
)
from typing import Optional

router = APIRouter(prefix="/recipes", tags=["recipes"])


def _avg_rating(recipe: Recipe) -> Optional[float]:
    if not recipe.ratings:
        return None
    return round(sum(r.score for r in recipe.ratings) / len(recipe.ratings), 1)


def _to_out(recipe: Recipe) -> RecipeOut:
    data = RecipeOut.model_validate(recipe)
    data.average_rating = _avg_rating(recipe)
    return data

@router.post("", response_model=RecipeOut, status_code=201)
async def create_recipe(
    body: RecipeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    recipe = Recipe(
        user_id=current_user.id,
        title=body.title,
        description=body.description,
        ingredients=[i.model_dump() for i in body.ingredients],
        steps=body.steps,
        cuisine_type=body.cuisine_type,
        prep_time_minutes=body.prep_time_minutes,
        cook_time_minutes=body.cook_time_minutes,
        servings=body.servings,
    )
    db.add(recipe)
    await db.flush()
    await db.refresh(recipe, ["ratings"])
    return _to_out(recipe)

@router.get("", response_model=RecipeListOut)
async def list_recipes(
    ingredient: Optional[list[str]] = Query(default=None),
    cuisine: Optional[str] = None,
    dietary: Optional[str] = None,
    max_prep_time: Optional[int] = None,
    sort: str = "newest",
    page: int = 1,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    limit = min(limit, 50)
    q = select(Recipe).options(selectinload(Recipe.ratings))

    if cuisine:
        q = q.where(Recipe.cuisine_type.ilike(f"%{cuisine}%"))
    if max_prep_time:
        q = q.where(Recipe.prep_time_minutes <= max_prep_time)
    if sort == "newest":
        q = q.order_by(Recipe.created_at.desc())

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar()

    q = q.offset((page - 1) * limit).limit(limit)
    recipes = (await db.execute(q)).scalars().all()

    return RecipeListOut(
        results=[_to_out(r) for r in recipes],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/{recipe_id}", response_model=RecipeOut)
async def get_recipe(recipe_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Recipe)
        .options(selectinload(Recipe.ratings))
        .where(Recipe.id == recipe_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return _to_out(recipe)


@router.patch("/{recipe_id}", response_model=RecipeOut)
async def update_recipe(
    recipe_id: str,
    body: RecipeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Recipe)
        .options(selectinload(Recipe.ratings))
        .where(Recipe.id == recipe_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    if recipe.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(recipe, field, value)

    await db.flush()
    await db.refresh(recipe, ["ratings"])
    return _to_out(recipe)


@router.delete("/{recipe_id}", status_code=204)
async def delete_recipe(
    recipe_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Recipe).where(Recipe.id == recipe_id))
    recipe = result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    if recipe.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    await db.delete(recipe)

@router.post("/{recipe_id}/ratings", response_model=RatingOut)
async def rate_recipe(
    recipe_id: str,
    body: RatingCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Recipe)
        .options(selectinload(Recipe.ratings))
        .where(Recipe.id == recipe_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    existing = await db.execute(
        select(Rating).where(
            Rating.user_id == current_user.id,
            Rating.recipe_id == recipe_id,
        )
    )
    rating = existing.scalar_one_or_none()

    if rating:
        rating.score = body.score
    else:
        rating = Rating(
            user_id=current_user.id,
            recipe_id=recipe_id,
            score=body.score,
        )
        db.add(rating)

    await db.flush()
    await db.refresh(recipe, ["ratings"])

    return RatingOut(
        recipe_id=recipe_id,
        user_id=current_user.id,
        score=body.score,
        recipe_average_rating=_avg_rating(recipe),
    )


@router.post("/{recipe_id}/save", status_code=200)
async def save_recipe(
    recipe_id: str,
    body: SaveRecipeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Recipe).where(Recipe.id == recipe_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Recipe not found")

    existing = await db.execute(
        select(SavedRecipe).where(
            SavedRecipe.user_id == current_user.id,
            SavedRecipe.recipe_id == recipe_id,
        )
    )
    saved = existing.scalar_one_or_none()

    if saved:
        saved.collection_id = body.collection_id
    else:
        saved = SavedRecipe(
            user_id=current_user.id,
            recipe_id=recipe_id,
            collection_id=body.collection_id,
        )
        db.add(saved)

    return {
        "saved": True,
        "recipe_id": recipe_id,
        "collection_id": body.collection_id,
    }