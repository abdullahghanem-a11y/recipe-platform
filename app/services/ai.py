import json
from app.core.config import settings


# ── dish recognition (hosted HF API) ─────────────────────────────────

def _call_image_classification(image_bytes: bytes) -> list:
    import httpx
    response = httpx.post(
        "https://router.huggingface.co/hf-inference/models/google/vit-base-patch16-224",
        headers={
            "Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}",
            "Content-Type": "image/jpeg",
        },
        content=image_bytes,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def recognize_dish(image_bytes: bytes) -> dict:
    results = _call_image_classification(image_bytes)
    top = results[0]
    return {
        "identified_dish": top["label"].replace("_", " ").lower(),
        "confidence": round(top["score"], 4),
        "top_3": [
            {"label": r["label"].replace("_", " ").lower(), "score": round(r["score"], 4)}
            for r in results[:3]
        ],
    }


def image_is_food(image_bytes: bytes, threshold: float = 0.3) -> bool:
    """Returns True if the top prediction score meets the threshold."""
    results = _call_image_classification(image_bytes)
    return results[0]["score"] >= threshold


# ── recipe generation ─────────────────────────────────────────────────

RECIPE_SCHEMA = """{
  "title": "string",
  "description": "string",
  "ingredients": [{"name": "string", "quantity": "string", "unit": "string"}],
  "steps": ["string"],
  "cuisine_type": "string",
  "prep_time_minutes": integer,
  "cook_time_minutes": integer,
  "servings": integer
}"""

SYSTEM_PROMPT = f"""You are a recipe generation assistant. You must ONLY respond with a valid JSON object.
Do not include any explanation, preamble, or markdown formatting like ```json.
The JSON must follow this exact schema:
{RECIPE_SCHEMA}

Example response for ingredients ["eggs", "cheese", "butter"]:
{{"title":"Classic Cheese Omelette","description":"A quick and fluffy omelette.","ingredients":[{{"name":"eggs","quantity":"3","unit":"whole"}},{{"name":"cheese","quantity":"50","unit":"g"}},{{"name":"butter","quantity":"1","unit":"tbsp"}}],"steps":["Crack eggs into bowl and whisk","Melt butter in pan over medium heat","Pour in eggs, add cheese, fold and serve"],"cuisine_type":"French","prep_time_minutes":5,"cook_time_minutes":5,"servings":1}}"""


def _extract_json(text: str) -> dict:
    text = text.strip()

    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except Exception:
                continue

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass

    raise ValueError(f"Could not extract valid JSON from model output: {text[:200]}")


def _normalize_recipe(recipe: dict) -> dict:
    ingredients = recipe.get("ingredients", [])
    normalized = []
    for item in ingredients:
        if isinstance(item, str):
            parts = item.strip().split()
            if len(parts) >= 3:
                normalized.append({"name": " ".join(parts[2:]), "quantity": parts[0], "unit": parts[1]})
            elif len(parts) == 2:
                normalized.append({"name": parts[1], "quantity": parts[0], "unit": ""})
            else:
                normalized.append({"name": item, "quantity": "1", "unit": ""})
        elif isinstance(item, dict):
            normalized.append({
                "name": item.get("name", ""),
                "quantity": str(item.get("quantity", "1")),
                "unit": item.get("unit", ""),
            })
    recipe["ingredients"] = normalized
    recipe["steps"] = [s if isinstance(s, str) else str(s) for s in recipe.get("steps", [])]
    for field in ["prep_time_minutes", "cook_time_minutes", "servings"]:
        try:
            recipe[field] = int(recipe.get(field, 0))
        except (ValueError, TypeError):
            recipe[field] = 0
    return recipe


async def generate_recipe(ingredients: list[str], dietary_preferences: list[str] = None) -> dict:
    import asyncio
    from huggingface_hub import InferenceClient

    dietary_note = ""
    if dietary_preferences:
        dietary_note = f" The recipe must be suitable for: {', '.join(dietary_preferences)}."

    user_message = f"Generate a recipe using these available ingredients: {', '.join(ingredients)}.{dietary_note}"

    def _call():
        client = InferenceClient(
            model="Qwen/Qwen2.5-7B-Instruct",
            token=settings.HUGGINGFACE_API_KEY,
            timeout=60,
        )
        response = client.chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=700,
            temperature=0.7,
        )
        return response.choices[0].message.content

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, _call)
    recipe = _extract_json(response)
    return _normalize_recipe(recipe)


# ── nutritional analysis ─────────────────────────────────────────────

async def analyze_nutrition(ingredients: list[str], servings: int = 1) -> dict:
    import asyncio
    from huggingface_hub import InferenceClient

    ingredient_text = "\n".join(f"- {i}" for i in ingredients)
    user_message = f"""Estimate the total nutritional content for {servings} serving(s) of a dish made with:
{ingredient_text}

Respond ONLY with a JSON object with these exact keys:
{{"calories": number, "protein_g": number, "carbohydrates_g": number, "fat_g": number, "fiber_g": number}}"""

    def _call():
        client = InferenceClient(
            model="Qwen/Qwen2.5-7B-Instruct",
            token=settings.HUGGINGFACE_API_KEY,
            timeout=30,
        )
        response = client.chat_completion(
            messages=[
                {"role": "system", "content": "You are a nutrition expert. Respond only with valid JSON, no explanation."},
                {"role": "user", "content": user_message},
            ],
            max_tokens=150,
            temperature=0.1,
        )
        return response.choices[0].message.content

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, _call)
    nutrition = _extract_json(response)
    nutrition["disclaimer"] = "Values are AI-estimated and not clinically verified."
    return nutrition


# ── ingredient substitution ──────────────────────────────────────────

async def get_substitutions(ingredient: str, restriction: str = None) -> dict:
    import asyncio
    from huggingface_hub import InferenceClient

    restriction_note = f" for someone who is {restriction}" if restriction else ""
    user_message = f"""Suggest 4 cooking substitutes for "{ingredient}"{restriction_note}.
For each substitute, provide a similarity score between 0 and 1 and a 1-2 sentence explanation of how it affects taste and texture.
Respond ONLY with this JSON structure:
{{
  "original_ingredient": "{ingredient}",
  "restriction": "{restriction or ''}",
  "substitutions": [
    {{"substitute": "name", "similarity_score": 0.9, "effect": "explanation"}},
    {{"substitute": "name", "similarity_score": 0.8, "effect": "explanation"}}
  ]
}}"""

    def _call():
        client = InferenceClient(
            model="Qwen/Qwen2.5-7B-Instruct",
            token=settings.HUGGINGFACE_API_KEY,
            timeout=30,
        )
        response = client.chat_completion(
            messages=[
                {"role": "system", "content": "You are a culinary expert. Respond only with valid JSON, no explanation or markdown."},
                {"role": "user", "content": user_message},
            ],
            max_tokens=400,
            temperature=0.3,
        )
        return response.choices[0].message.content

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, _call)
    return _extract_json(response)


async def _explain_substitutions(
    original: str, substitutes: list[str], restriction: str = None
) -> dict:
    import asyncio
    from huggingface_hub import InferenceClient

    restriction_note = f" (dietary restriction: {restriction})" if restriction else ""
    subs_list = ", ".join(substitutes)

    user_message = f"""For cooking, briefly explain how each substitute affects taste and texture when replacing "{original}"{restriction_note}.
Substitutes: {subs_list}
Respond ONLY with a JSON object mapping each substitute name to a 1-2 sentence explanation.
Example: {{"olive oil": "Lighter flavour. Works well in most savoury dishes."}}"""

    def _call():
        client = InferenceClient(
            model="Qwen/Qwen2.5-7B-Instruct",
            token=settings.HUGGINGFACE_API_KEY,
            timeout=30,
        )
        response = client.chat_completion(
            messages=[
                {"role": "system", "content": "You are a cooking expert. Respond only with valid JSON, no explanation."},
                {"role": "user", "content": user_message},
            ],
            max_tokens=300,
            temperature=0.3,
        )
        return response.choices[0].message.content

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, _call)
        return _extract_json(response)
    except Exception:
        return {}
