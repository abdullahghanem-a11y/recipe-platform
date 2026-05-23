import time
import hashlib
from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from pydantic import BaseModel, field_validator
from app.core.database import get_db
from app.core.redis import make_cache_key, get_cached, set_cached, check_rate_limit
from app.core.security import get_api_key
from app.models.community import InferenceLog
from app.models.recipe import Recipe
from app.services import ai as ai_service

router = APIRouter(prefix="/ai", tags=["ai"])


# ── Request body schemas ──────────────────────────────────────────────

class GenerateRequest(BaseModel):
    ingredients: list[str]
    dietary_preferences: list[str] = []

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, v):
        if len(v) == 0:
            raise ValueError("At least one ingredient required")
        if len(v) > 20:
            raise ValueError("Maximum 20 ingredients allowed")
        return v


class NutritionRequest(BaseModel):
    ingredients: list[str]
    servings: int = 1

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, v):
        if len(v) == 0:
            raise ValueError("At least one ingredient required")
        if len(v) > 20:
            raise ValueError("Maximum 20 ingredients allowed")
        return v


class SubstituteRequest(BaseModel):
    ingredient: str
    restriction: Optional[str] = None

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


# ── POST /ai/recognize ────────────────────────────────────────────────

@router.post("/recognize")
async def recognize(
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_api_key),
):
    await check_rate_limit("/ai/recognize", _.id)
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=415, detail="Only JPEG and PNG images are supported")

    image_bytes = await image.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image must be under 10MB")

    if not ai_service.image_is_food(image_bytes):
        raise HTTPException(
            status_code=422,
            detail={"error": "not_food", "message": "Image does not appear to contain food."},
        )

    cache_key = make_cache_key("recognize", {"hash": _image_hash(image_bytes)})
    cached = await get_cached(cache_key)
    if cached:
        return cached

    t0 = time.perf_counter()
    recognition = ai_service.recognize_dish(image_bytes)
    processing_ms = int((time.perf_counter() - t0) * 1000)

    dish_name = recognition["identified_dish"]
    result = await db.execute(
        select(Recipe).where(Recipe.title.ilike(f"%{dish_name.split()[0]}%")).limit(5)
    )
    matching = result.scalars().all()
    recognition["matching_recipes"] = [
        {"recipe_id": r.id, "title": r.title} for r in matching
    ]

    await set_cached(cache_key, recognition)
    await _log_inference(db, "/ai/recognize", _image_hash(image_bytes), recognition, processing_ms)
    return recognition


# ── POST /ai/generate ─────────────────────────────────────────────────

@router.post("/generate")
async def generate(
    body: GenerateRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_api_key),
):
    await check_rate_limit("/ai/generate", _.id)

    cache_key = make_cache_key("generate", {"ingredients": sorted(body.ingredients)})
    cached = await get_cached(cache_key)
    if cached:
        return cached

    t0 = time.perf_counter()
    try:
        recipe = await ai_service.generate_recipe(body.ingredients, body.dietary_preferences)
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
                "message": "Could not parse a valid recipe from the model. Please try again.",
                "retry_after_seconds": 10,
            },
        )

    processing_ms = int((time.perf_counter() - t0) * 1000)
    await set_cached(cache_key, recipe)
    await _log_inference(db, "/ai/generate", cache_key, recipe, processing_ms)
    return recipe


# ── POST /ai/nutrition ────────────────────────────────────────────────

@router.post("/nutrition")
async def nutrition(
    body: NutritionRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_api_key),
):
    await check_rate_limit("/ai/nutrition", _.id)

    cache_key = make_cache_key("nutrition", {"ingredients": sorted(body.ingredients), "servings": body.servings})
    cached = await get_cached(cache_key)
    if cached:
        return cached

    t0 = time.perf_counter()
    try:
        result = await ai_service.analyze_nutrition(body.ingredients, body.servings)
    except Exception:
        raise HTTPException(status_code=503, detail="Nutrition analysis temporarily unavailable")

    processing_ms = int((time.perf_counter() - t0) * 1000)
    await set_cached(cache_key, result)
    await _log_inference(db, "/ai/nutrition", cache_key, result, processing_ms)
    return result


# ── POST /ai/substitute ───────────────────────────────────────────────

@router.post("/substitute")
async def substitute(
    body: SubstituteRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_api_key),
):
    await check_rate_limit("/ai/substitute", _.id)

    cache_key = make_cache_key("substitute", {"ingredient": body.ingredient, "restriction": body.restriction})
    cached = await get_cached(cache_key)
    if cached:
        return cached

    t0 = time.perf_counter()
    try:
        result = await ai_service.get_substitutions(body.ingredient, body.restriction)
    except Exception:
        raise HTTPException(status_code=503, detail="Substitution service temporarily unavailable")

    processing_ms = int((time.perf_counter() - t0) * 1000)
    await set_cached(cache_key, result)
    await _log_inference(db, "/ai/substitute", cache_key, result, processing_ms)
    return result


# ── POST /ai/assist ───────────────────────────────────────────────────

@router.post("/assist")
async def assist(
    image: Optional[UploadFile] = File(default=None),
    ingredients: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_api_key),
):
    await check_rate_limit("/ai/assist", _.id)
    import json as _json

    # Parse ingredients from form field (JSON string)
    ingredient_list = []
    if ingredients:
        try:
            ingredient_list = _json.loads(ingredients)
        except Exception:
            raise HTTPException(status_code=422, detail="ingredients must be a JSON array string")
        if len(ingredient_list) > 20:
            raise HTTPException(status_code=422, detail="Maximum 20 ingredients allowed")

    # At least one input required
    if not image and not ingredient_list:
        raise HTTPException(
            status_code=422,
            detail="At least one of image or ingredients must be provided",
        )

    image_bytes = None
    if image:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=415, detail="Only JPEG and PNG images are supported")
        image_bytes = await image.read()
        if not ai_service.image_is_food(image_bytes):
            raise HTTPException(
                status_code=422,
                detail={"error": "not_food", "message": "Image does not appear to contain food."},
            )

    # Determine input type
    if image_bytes and ingredient_list:
        input_type = "photo_and_ingredients"
    elif image_bytes:
        input_type = "photo_only"
    else:
        input_type = "ingredients_only"

    response = {"input_type": input_type}
    processing_times = {}
    total_start = time.perf_counter()

    # ── Task: dish recognition (only if image provided) ──
    if image_bytes:
        t0 = time.perf_counter()
        try:
            recognition = ai_service.recognize_dish(image_bytes)
            result = await db.execute(
                select(Recipe).where(
                    Recipe.title.ilike(f"%{recognition['identified_dish'].split()[0]}%")
                ).limit(5)
            )
            matching = result.scalars().all()
            recognition["matching_recipes"] = [
                {"recipe_id": r.id, "title": r.title} for r in matching
            ]
            response["dish_recognition"] = recognition
        except Exception as e:
            response["dish_recognition"] = {"error": "recognition_failed", "message": str(e)}
        processing_times["recognition"] = int((time.perf_counter() - t0) * 1000)

    # ── Task: recipe generation (only if ingredients provided) ──
    if ingredient_list:
        t0 = time.perf_counter()
        try:
            response["generated_recipe"] = await ai_service.generate_recipe(ingredient_list)
        except Exception as e:
            response["generated_recipe"] = {"error": "generation_failed", "message": str(e)}
        processing_times["generation"] = int((time.perf_counter() - t0) * 1000)

    # ── Task: nutritional analysis (only if ingredients provided) ──
    if ingredient_list:
        t0 = time.perf_counter()
        try:
            response["nutrition_per_serving"] = await ai_service.analyze_nutrition(ingredient_list)
        except Exception as e:
            response["nutrition_per_serving"] = {"error": "nutrition_failed", "message": str(e)}
        processing_times["nutrition"] = int((time.perf_counter() - t0) * 1000)

    # ── Task: substitutions (only if ingredients provided) ──
    if ingredient_list:
        t0 = time.perf_counter()
        try:
            first_ingredient = ingredient_list[0]
            subs = await ai_service.get_substitutions(first_ingredient)
            response["suggested_substitutions"] = [
                {"original": first_ingredient, "substitute": s["substitute"], "reason": s["effect"]}
                for s in subs.get("substitutions", [])[:3]
            ]
        except Exception as e:
            response["suggested_substitutions"] = {"error": "substitution_failed", "message": str(e)}
        processing_times["substitutions"] = int((time.perf_counter() - t0) * 1000)

    processing_times["total"] = int((time.perf_counter() - total_start) * 1000)
    response["processing_time_ms"] = processing_times

    return response