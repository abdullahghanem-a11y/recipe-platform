from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, String
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.recipe import Recipe
from app.models.community import Rating, SavedRecipe, Comment
from app.schemas.recipe import (
    RecipeCreate, RecipeUpdate, RecipeOut, RecipeListOut,
    RatingCreate, RatingOut, SaveRecipeRequest,
    CommentCreate, CommentUpdate, CommentOut, CommentListOut,
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
    data.comment_count = len(recipe.comments) if recipe.comments else 0
    return data


# ── recipes CRUD ─────────────────────────────────────────────────────

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
    await db.refresh(recipe, ["ratings", "comments"])
    return _to_out(recipe)


@router.get("", response_model=RecipeListOut)
async def list_recipes(
    # ── text search ──
    q: Optional[str] = Query(default=None, description="Search in title and description"),
    # ── filters ──
    ingredient: Optional[list[str]] = Query(default=None, description="Filter by ingredient name(s)"),
    cuisine: Optional[str] = Query(default=None, description="Filter by cuisine type"),
    dietary: Optional[str] = Query(default=None, description="Filter by dietary preference (matches user tags)"),
    max_prep_time: Optional[int] = Query(default=None, description="Max prep time in minutes"),
    max_cook_time: Optional[int] = Query(default=None, description="Max cook time in minutes"),
    min_rating: Optional[float] = Query(default=None, ge=1, le=5, description="Minimum average rating"),
    # ── sort ──
    sort: str = Query(default="newest", enum=["newest", "oldest", "top_rated", "most_commented"]),
    # ── pagination ──
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    limit = min(limit, 50)
    q_stmt = select(Recipe).options(
        selectinload(Recipe.ratings),
        selectinload(Recipe.comments),
    )

    # ── text search in title / description ──
    if q:
        search = f"%{q.strip()}%"
        q_stmt = q_stmt.where(
            Recipe.title.ilike(search) | Recipe.description.ilike(search)
        )

    # ── cuisine filter ──
    if cuisine:
        q_stmt = q_stmt.where(Recipe.cuisine_type.ilike(f"%{cuisine}%"))

    # ── time filters ──
    if max_prep_time is not None:
        q_stmt = q_stmt.where(Recipe.prep_time_minutes <= max_prep_time)
    if max_cook_time is not None:
        q_stmt = q_stmt.where(Recipe.cook_time_minutes <= max_cook_time)

    # ── ingredient filter (JSON array contains) ──
    if ingredient:
        for ing in ingredient:
            q_stmt = q_stmt.where(
                cast(Recipe.ingredients, String).ilike(f"%{ing}%")
            )

    # ── sorting ──
    if sort == "newest":
        q_stmt = q_stmt.order_by(Recipe.created_at.desc())
    elif sort == "oldest":
        q_stmt = q_stmt.order_by(Recipe.created_at.asc())
    elif sort == "top_rated":
        # Subquery: avg rating per recipe
        avg_sub = (
            select(Rating.recipe_id, func.avg(Rating.score).label("avg_score"))
            .group_by(Rating.recipe_id)
            .subquery()
        )
        q_stmt = q_stmt.outerjoin(avg_sub, Recipe.id == avg_sub.c.recipe_id)
        q_stmt = q_stmt.order_by(avg_sub.c.avg_score.desc().nulls_last())
    elif sort == "most_commented":
        comment_sub = (
            select(Comment.recipe_id, func.count(Comment.id).label("comment_count"))
            .group_by(Comment.recipe_id)
            .subquery()
        )
        q_stmt = q_stmt.outerjoin(comment_sub, Recipe.id == comment_sub.c.recipe_id)
        q_stmt = q_stmt.order_by(comment_sub.c.comment_count.desc().nulls_last())

    # ── fetch all for rating filter (applied in Python after eager load) ──
    all_recipes = (await db.execute(q_stmt)).scalars().all()

    # ── min_rating filter (needs computed avg, done in Python) ──
    if min_rating is not None:
        all_recipes = [
            r for r in all_recipes
            if r.ratings and _avg_rating(r) >= min_rating
        ]

    # ── dietary filter (recipe ingredients vs user dietary tag) ──
    if dietary:
        dietary_lower = dietary.lower()
        # Simple keyword match against ingredient names
        DIETARY_EXCLUDE = {
            "vegan": ["meat", "chicken", "beef", "pork", "fish", "egg", "milk", "cheese", "butter", "cream", "honey"],
            "vegetarian": ["meat", "chicken", "beef", "pork", "fish", "bacon", "ham"],
            "gluten-free": ["flour", "wheat", "bread", "pasta", "barley", "rye"],
            "dairy-free": ["milk", "cheese", "butter", "cream", "yogurt"],
            "nut-free": ["almond", "cashew", "walnut", "pecan", "peanut", "hazelnut", "pistachio"],
        }
        excluded = DIETARY_EXCLUDE.get(dietary_lower, [])
        if excluded:
            def passes_dietary(recipe: Recipe) -> bool:
                ing_names = " ".join(
                    i.get("name", "").lower() if isinstance(i, dict) else str(i).lower()
                    for i in recipe.ingredients
                )
                return not any(ex in ing_names for ex in excluded)
            all_recipes = [r for r in all_recipes if passes_dietary(r)]

    total = len(all_recipes)
    paginated = all_recipes[(page - 1) * limit: page * limit]

    return RecipeListOut(
        results=[_to_out(r) for r in paginated],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/{recipe_id}", response_model=RecipeOut)
async def get_recipe(recipe_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Recipe)
        .options(selectinload(Recipe.ratings), selectinload(Recipe.comments))
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
        .options(selectinload(Recipe.ratings), selectinload(Recipe.comments))
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
    await db.refresh(recipe, ["ratings", "comments"])
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


# ── ratings ───────────────────────────────────────────────────────────

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


# ── save ──────────────────────────────────────────────────────────────

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


# ── comments ──────────────────────────────────────────────────────────

@router.post("/{recipe_id}/comments", response_model=CommentOut, status_code=201)
async def add_comment(
    recipe_id: str,
    body: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Recipe).where(Recipe.id == recipe_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Recipe not found")

    comment = Comment(
        user_id=current_user.id,
        recipe_id=recipe_id,
        body=body.body,
    )
    db.add(comment)
    await db.flush()
    await db.refresh(comment)

    return CommentOut(
        id=comment.id,
        recipe_id=comment.recipe_id,
        user_id=comment.user_id,
        username=current_user.username,
        body=comment.body,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


@router.get("/{recipe_id}/comments", response_model=CommentListOut)
async def list_comments(
    recipe_id: str,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Recipe).where(Recipe.id == recipe_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Recipe not found")

    count_result = await db.execute(
        select(func.count(Comment.id)).where(Comment.recipe_id == recipe_id)
    )
    total = count_result.scalar()

    comments_result = await db.execute(
        select(Comment, User.username)
        .join(User, Comment.user_id == User.id)
        .where(Comment.recipe_id == recipe_id)
        .order_by(Comment.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = comments_result.all()

    return CommentListOut(
        results=[
            CommentOut(
                id=c.id,
                recipe_id=c.recipe_id,
                user_id=c.user_id,
                username=username,
                body=c.body,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c, username in rows
        ],
        total=total,
        page=page,
        limit=limit,
    )


@router.patch("/{recipe_id}/comments/{comment_id}", response_model=CommentOut)
async def update_comment(
    recipe_id: str,
    comment_id: str,
    body: CommentUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Comment).where(
            Comment.id == comment_id,
            Comment.recipe_id == recipe_id,
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    comment.body = body.body
    await db.flush()
    await db.refresh(comment)

    return CommentOut(
        id=comment.id,
        recipe_id=comment.recipe_id,
        user_id=comment.user_id,
        username=current_user.username,
        body=comment.body,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


@router.delete("/{recipe_id}/comments/{comment_id}", status_code=204)
async def delete_comment(
    recipe_id: str,
    comment_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Comment).where(
            Comment.id == comment_id,
            Comment.recipe_id == recipe_id,
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db.delete(comment)
