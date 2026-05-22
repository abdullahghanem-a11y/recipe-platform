from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import Optional
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.community import SavedRecipe
from app.models.recipe import Recipe
from app.schemas.user import UserOut, UserUpdate
from app.schemas.recipe import RecipeOut, RecipeListOut

router = APIRouter(prefix="/users", tags=["users"])


def _to_out(recipe: Recipe) -> RecipeOut:
    data = RecipeOut.model_validate(recipe)
    if recipe.ratings:
        data.average_rating = round(
            sum(r.score for r in recipe.ratings) / len(recipe.ratings), 1
        )
    return data


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
async def update_me(
    body: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.username is not None:
        current_user.username = body.username
    if body.dietary_preferences is not None:
        current_user.dietary_preferences = body.dietary_preferences

    await db.flush()
    await db.refresh(current_user)
    return current_user


@router.get("/me/saved", response_model=RecipeListOut)
async def get_saved_recipes(
    collection_id: Optional[str] = Query(default=None),
    page: int = 1,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    limit = min(limit, 50)

    saved_q = select(SavedRecipe.recipe_id).where(
        SavedRecipe.user_id == current_user.id
    )
    if collection_id:
        saved_q = saved_q.where(SavedRecipe.collection_id == collection_id)

    saved_result = await db.execute(saved_q)
    recipe_ids = [row[0] for row in saved_result.fetchall()]

    if not recipe_ids:
        return RecipeListOut(results=[], total=0, page=page, limit=limit)

    from sqlalchemy import func
    q = (
        select(Recipe)
        .options(selectinload(Recipe.ratings))
        .where(Recipe.id.in_(recipe_ids))
    )

    total_result = await db.execute(
        select(func.count()).select_from(q.subquery())
    )
    total = total_result.scalar()

    q = q.offset((page - 1) * limit).limit(limit)
    recipes = (await db.execute(q)).scalars().all()

    return RecipeListOut(
        results=[_to_out(r) for r in recipes],
        total=total,
        page=page,
        limit=limit,
    )