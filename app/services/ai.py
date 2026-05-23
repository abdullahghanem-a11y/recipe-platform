import json
from PIL import Image
import io
from app.core.config import settings

# ── lazy model holders ──────────────────────────────────────────────
_vit_pipe = None
_clip_model = None
_clip_processor = None
_nutrition_model = None


def _load_vit():
    global _vit_pipe
    if _vit_pipe is None:
        from transformers import pipeline
        _vit_pipe = pipeline(
            "image-classification",
            model="google/vit-base-patch16-224",
        )
    return _vit_pipe


def _load_clip():
    global _clip_model, _clip_processor
    if _clip_model is None:
        from transformers import CLIPModel, CLIPProcessor
        _clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        _clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
    return _clip_model, _clip_processor


def _load_nutrition():
    global _nutrition_model
    if _nutrition_model is None:
        from transformers import pipeline
        _nutrition_model = pipeline(
            "text-generation",
            model="llmware-ai/slim-extract-tool",
            max_new_tokens=200,
        )
    return _nutrition_model


# ── dish recognition ─────────────────────────────────────────────────

def recognize_dish(image_bytes: bytes) -> dict:
    pipe = _load_vit()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    results = pipe(image, top_k=3)

    top = results[0]
    return {
        "identified_dish": top["label"].replace("_", " ").lower(),
        "confidence": round(top["score"], 4),
        "top_3": [
            {"label": r["label"].replace("_", " ").lower(), "score": round(r["score"], 4)}
            for r in results
        ],
    }


def image_is_food(image_bytes: bytes, threshold: float = 0.3) -> bool:
    """Returns True if the image passes the food confidence gate."""
    pipe = _load_vit()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    results = pipe(image, top_k=1)
    return results[0]["score"] >= threshold


# ── recipe generation (hosted HF API) ────────────────────────────────

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
    """
    Robustly extract a JSON object from model output.
    Handles markdown fences, preamble text, and trailing content.
    """
    text = text.strip()

    # 1. Strip markdown fences: ```json ... ``` or ``` ... ```
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

    # 2. Try parsing the whole text directly
    try:
        return json.loads(text)
    except Exception:
        pass

    # 3. Find the first { and last } and try that substring
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass

    raise ValueError(f"Could not extract valid JSON from model output: {text[:200]}")


async def generate_recipe(ingredients: list[str], dietary_preferences: list[str] = None) -> dict:
    from huggingface_hub import InferenceClient

    dietary_note = ""
    if dietary_preferences:
        dietary_note = f" The recipe must be suitable for: {', '.join(dietary_preferences)}."

    user_message = f"Generate a recipe using these available ingredients: {', '.join(ingredients)}.{dietary_note}"

    prompt = f"<|system|>\n{SYSTEM_PROMPT}\n<|user|>\n{user_message}\n<|assistant|>\n"

    client = InferenceClient(
        model="HuggingFaceH4/zephyr-7b-beta",
        token=settings.HUGGINGFACE_API_KEY,
        timeout=60,
    )

    response = client.text_generation(
        prompt,
        max_new_tokens=700,
        temperature=0.7,
        return_full_text=False,
    )

    return _extract_json(response)


# ── nutritional analysis ─────────────────────────────────────────────

async def analyze_nutrition(ingredients: list[str], servings: int = 1) -> dict:
    from huggingface_hub import InferenceClient

    ingredient_text = "\n".join(f"- {i}" for i in ingredients)
    prompt = f"""Estimate the total nutritional content for {servings} serving(s) of a dish made with:
{ingredient_text}

Respond ONLY with a JSON object with these exact keys:
{{"calories": number, "protein_g": number, "carbohydrates_g": number, "fat_g": number, "fiber_g": number}}"""

    client = InferenceClient(
        model="mistralai/Mistral-7B-Instruct-v0.1",
        token=settings.HUGGINGFACE_API_KEY,
        timeout=30,
    )

    response = client.text_generation(
        prompt,
        max_new_tokens=150,
        return_full_text=False,
    )

    nutrition = _extract_json(response)
    nutrition["disclaimer"] = "Values are AI-estimated and not clinically verified."
    return nutrition


# ── ingredient substitution ──────────────────────────────────────────

SUBSTITUTE_CANDIDATES = [
    "butter", "olive oil", "coconut oil", "vegetable oil", "applesauce",
    "milk", "oat milk", "almond milk", "soy milk", "coconut milk",
    "eggs", "flax eggs", "chia eggs", "aquafaba",
    "all-purpose flour", "almond flour", "oat flour", "rice flour", "chickpea flour",
    "sugar", "honey", "maple syrup", "agave", "stevia",
    "beef", "chicken", "tofu", "tempeh", "lentils", "jackfruit",
    "cheese", "nutritional yeast", "cashew cheese", "vegan cheese",
    "cream", "coconut cream", "silken tofu",
    "breadcrumbs", "gluten-free breadcrumbs", "crushed crackers", "oats",
    "pancetta", "turkey bacon", "smoked tempeh", "mushroom bacon",
    "sour cream", "greek yogurt", "coconut yogurt",
    "white wine", "apple juice", "white grape juice", "chicken broth",
    "soy sauce", "tamari", "coconut aminos",
    "mayonnaise", "avocado", "hummus",
]


async def get_substitutions(ingredient: str, restriction: str = None) -> dict:
    import torch
    model, processor = _load_clip()

    # Encode source ingredient
    source_inputs = processor(text=[ingredient], return_tensors="pt", padding=True)
    with torch.no_grad():
        source_emb = model.get_text_features(**source_inputs)
        source_emb = source_emb / source_emb.norm(dim=-1, keepdim=True)

    # Encode candidates
    cand_inputs = processor(text=SUBSTITUTE_CANDIDATES, return_tensors="pt", padding=True)
    with torch.no_grad():
        cand_embs = model.get_text_features(**cand_inputs)
        cand_embs = cand_embs / cand_embs.norm(dim=-1, keepdim=True)

    # Cosine similarity
    similarities = (source_emb @ cand_embs.T).squeeze(0).tolist()
    scored = sorted(
        zip(SUBSTITUTE_CANDIDATES, similarities), key=lambda x: x[1], reverse=True
    )

    # Remove the ingredient itself and get top 5
    top = [
        (s, score)
        for s, score in scored
        if s.lower() != ingredient.lower()
    ][:5]

    # Get explanations from HF API
    explanations = await _explain_substitutions(ingredient, [s for s, _ in top], restriction)

    return {
        "original_ingredient": ingredient,
        "restriction": restriction,
        "substitutions": [
            {
                "substitute": sub,
                "similarity_score": round(score, 3),
                "effect": explanations.get(sub, "A suitable alternative for this ingredient."),
            }
            for sub, score in top
        ],
    }


async def _explain_substitutions(
    original: str, substitutes: list[str], restriction: str = None
) -> dict:
    from huggingface_hub import InferenceClient

    restriction_note = f" (dietary restriction: {restriction})" if restriction else ""
    subs_list = ", ".join(substitutes)

    prompt = f"""For cooking, briefly explain how each substitute affects taste and texture when replacing "{original}"{restriction_note}.
Substitutes: {subs_list}
Respond ONLY with a JSON object mapping each substitute name to a 1-2 sentence explanation.
Example: {{"substitute_name": "Slightly sweeter. Works well in baked goods."}}"""

    try:
        client = InferenceClient(
            model="mistralai/Mistral-7B-Instruct-v0.1",
            token=settings.HUGGINGFACE_API_KEY,
            timeout=30,
        )
        response = client.text_generation(
            prompt,
            max_new_tokens=300,
            return_full_text=False,
        )
        return _extract_json(response)
    except Exception:
        return {}