import time
import hashlib
from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from app.core.database import get_db
from app.core.redis import make_cache_key, get_cached, set_cached
from app.models.community import InferenceLog
from app.models.recipe import Recipe
from app.services import ai as ai_service

router = APIRouter(prefix="/ai", tags=["ai"])

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}


async def _log_inference(
    db: AsyncSession,
    endpoint: str,
    input_hash: str,
    result: dict,
    processing_ms: int,
    user_id: str = None,
):
    log = InferenceLog(
        user_id=user_id,
        endpoint=endpoint,
        input_hash=input_hash,
        result=result,
        processing_ms=processing_ms,
    )
    db.add(log)
    await db.flush()


def _image_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

@router.post("/recognize")
async def recognize(
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Only JPEG and PNG images are supported",
        )

    image_bytes = await image.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image must be under 10MB")

    if not ai_service.image_is_food(image_bytes):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "not_food",
                "message": "Image does not appear to contain food.",
            },
        )

    cache_key = make_cache_key("recognize", {"hash": _image_hash(image_bytes)})
    cached = await get_cached(cache_key)
    if cached:
        return cached

    t0 = time.perf_counter()
    recognition = ai_service.recognize_dish(image_bytes)
    processing_ms = int((time.perf_counter() - t0) * 1000)

    result = await db.execute(
        select(Recipe)
        .where(Recipe.title.ilike(f"%{recognition['identified_dish'].split()[0]}%"))
        .limit(5)
    )
    matching = result.scalars().all()
    recognition["matching_recipes"] = [
        {"recipe_id": r.id, "title": r.title} for r in matching
    ]

    await set_cached(cache_key, recognition)
    await _log_inference(
        db, "/ai/recognize", _image_hash(image_bytes), recognition, processing_ms
    )
    return recognition

@router.post("/generate")
async def generate(
    ingredients: list[str],
    dietary_preferences: list[str] = None,
    db: AsyncSession = Depends(get_db),
):
    if len(ingredients) > 20:
        raise HTTPException(
            status_code=422, detail="Maximum 20 ingredients allowed"
        )
    if len(ingredients) == 0:
        raise HTTPException(
            status_code=422, detail="At least one ingredient required"
        )

    cache_key = make_cache_key(
        "generate", {"ingredients": sorted(ingredients)}
    )
    cached = await get_cached(cache_key)
    if cached:
        return cached

    t0 = time.perf_counter()
    try:
        recipe = await ai_service.generate_recipe(ingredients, dietary_preferences)
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_unavailable",
                "message": "Recipe generation service is temporarily unavailable.",
                "retry_after_seconds": 30,
            },
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generation_failed",
                "message": "Could not parse a valid recipe. Please try again.",
                "retry_after_seconds": 10,
            },
        )

    processing_ms = int((time.perf_counter() - t0) * 1000)
    await set_cached(cache_key, recipe)
    await _log_inference(db, "/ai/generate", cache_key, recipe, processing_ms)
    return recipe

@router.post("/nutrition")
async def nutrition(
    ingredients: list[str],
    servings: int = 1,
    db: AsyncSession = Depends(get_db),
):
    if len(ingredients) > 20:
        raise HTTPException(
            status_code=422, detail="Maximum 20 ingredients allowed"
        )

    cache_key = make_cache_key(
        "nutrition",
        {"ingredients": sorted(ingredients), "servings": servings},
    )
    cached = await get_cached(cache_key)
    if cached:
        return cached

    t0 = time.perf_counter()
    try:
        result = await ai_service.analyze_nutrition(ingredients, servings)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Nutrition analysis temporarily unavailable",
        )

    processing_ms = int((time.perf_counter() - t0) * 1000)
    await set_cached(cache_key, result)
    await _log_inference(db, "/ai/nutrition", cache_key, result, processing_ms)
    return result

@router.post("/substitute")
async def substitute(
    ingredient: str,
    restriction: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    cache_key = make_cache_key(
        "substitute",
        {"ingredient": ingredient, "restriction": restriction},
    )
    cached = await get_cached(cache_key)
    if cached:
        return cached

    t0 = time.perf_counter()
    try:
        result = await ai_service.get_substitutions(ingredient, restriction)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Substitution service temporarily unavailable",
        )

    processing_ms = int((time.perf_counter() - t0) * 1000)
    await set_cached(cache_key, result)
    await _log_inference(db, "/ai/substitute", cache_key, result, processing_ms)
    return result


@router.post("/assist")
async def assist(
    image: Optional[UploadFile] = File(default=None),
    ingredients: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    import json as _json

    ingredient_list = []
    if ingredients:
        try:
            ingredient_list = _json.loads(ingredients)
        except Exception:
            raise HTTPException(
                status_code=422,
                detail="ingredients must be a valid JSON array string",
            )
        if len(ingredient_list) > 20:
            raise HTTPException(
                status_code=422, detail="Maximum 20 ingredients allowed"
            )

    if not image and not ingredient_list:
        raise HTTPException(
            status_code=422,
            detail="At least one of image or ingredients must be provided",
        )

    image_bytes = None
    if image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=415,
                detail="Only JPEG and PNG images are supported",
            )
        image_bytes = await image.read()
        if not ai_service.image_is_food(image_bytes):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "not_food",
                    "message": "Image does not appear to contain food.",
                },
            )

    if image_bytes and ingredient_list:
        input_type = "photo_and_ingredients"
    elif image_bytes:
        input_type = "photo_only"
    else:
        input_type = "ingredients_only"

    response = {"input_type": input_type}
    processing_times = {}
    total_start = time.perf_counter()

    # Task 1 — dish recognition (image only)
    if image_bytes:
        t0 = time.perf_counter()
        try:
            recognition = ai_service.recognize_dish(image_bytes)
            db_result = await db.execute(
                select(Recipe)
                .where(
                    Recipe.title.ilike(
                        f"%{recognition['identified_dish'].split()[0]}%"
                    )
                )
                .limit(5)
            )
            matching = db_result.scalars().all()
            recognition["matching_recipes"] = [
                {"recipe_id": r.id, "title": r.title} for r in matching
            ]
            response["dish_recognition"] = recognition
        except Exception as e:
            response["dish_recognition"] = {
                "error": "recognition_failed",
                "message": str(e),
            }
        processing_times["recognition"] = int(
            (time.perf_counter() - t0) * 1000
        )

    # Task 2 — recipe generation (ingredients only)
    if ingredient_list:
        t0 = time.perf_counter()
        try:
            response["generated_recipe"] = await ai_service.generate_recipe(
                ingredient_list
            )
        except Exception as e:
            response["generated_recipe"] = {
                "error": "generation_failed",
                "message": str(e),
            }
        processing_times["generation"] = int(
            (time.perf_counter() - t0) * 1000
        )

    # Task 3 — nutritional analysis (ingredients only)
    if ingredient_list:
        t0 = time.perf_counter()
        try:
            response["nutrition_per_serving"] = await ai_service.analyze_nutrition(
                ingredient_list
            )
        except Exception as e:
            response["nutrition_per_serving"] = {
                "error": "nutrition_failed",
                "message": str(e),
            }
        processing_times["nutrition"] = int(
            (time.perf_counter() - t0) * 1000
        )

    # Task 4 — substitutions (ingredients only)
    if ingredient_list:
        t0 = time.perf_counter()
        try:
            first_ingredient = ingredient_list[0]
            subs = await ai_service.get_substitutions(first_ingredient)
            response["suggested_substitutions"] = [
                {
                    "original": first_ingredient,
                    "substitute": s["substitute"],
                    "reason": s["effect"],
                }
                for s in subs.get("substitutions", [])[:3]
            ]
        except Exception as e:
            response["suggested_substitutions"] = {
                "error": "substitution_failed",
                "message": str(e),
            }
        processing_times["substitutions"] = int(
            (time.perf_counter() - t0) * 1000
        )

    processing_times["total"] = int(
        (time.perf_counter() - total_start) * 1000
    )
    response["processing_time_ms"] = processing_times
    return response