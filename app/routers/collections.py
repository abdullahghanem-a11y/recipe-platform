from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.community import Collection, SavedRecipe
from app.schemas.recipe import CollectionCreate, CollectionOut

router = APIRouter(prefix="/collections", tags=["collections"])


@router.post("", response_model=CollectionOut, status_code=201)
async def create_collection(
    body: CollectionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    collection = Collection(user_id=current_user.id, name=body.name)
    db.add(collection)
    await db.flush()
    await db.refresh(collection)
    return CollectionOut(
        id=collection.id,
        name=collection.name,
        user_id=collection.user_id,
        recipe_count=0,
        created_at=collection.created_at,
    )


@router.get("", response_model=list[CollectionOut])
async def list_collections(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Collection).where(Collection.user_id == current_user.id)
    )
    collections = result.scalars().all()

    out = []
    for c in collections:
        count_result = await db.execute(
            select(func.count()).where(SavedRecipe.collection_id == c.id)
        )
        count = count_result.scalar()
        out.append(
            CollectionOut(
                id=c.id,
                name=c.name,
                user_id=c.user_id,
                recipe_count=count,
                created_at=c.created_at,
            )
        )
    return out