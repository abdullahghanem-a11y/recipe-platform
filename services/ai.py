import time
import json
import hashlib
import httpx
from PIL import Image
import io
from app.core.config import settings

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

def recognize_dish(image_bytes: bytes) -> dict:
    pipe = _load_vit()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    results = pipe(image, top_k=3)

    top = results[0]
    return {
        "identified_dish": top["label"].replace("_", " ").lower(),
        "confidence": round(top["score"], 4),
        "top_3": [
            {
                "label": r["label"].replace("_", " ").lower(),
                "score": round(r["score"], 4),
            }
            for r in results
        ],
    }


def image_is_food(image_bytes: bytes, threshold: float = 0.3) -> bool:
    pipe = _load_vit()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    results = pipe(image, top_k=1)
    return results[0]["score"] >= threshold

SYSTEM_PROMPT = """You are a recipe generation assistant. You must ONLY respond 
with a valid JSON object. Do not include any explanation, preamble, or markdown 
formatting like ```json.
The JSON must follow this exact schema:
{
  "title": "string",
  "description": "string", 
  "ingredients": [{"name": "string", "quantity": "string", "unit": "string"}],
  "steps": ["string"],
  "cuisine_type": "string",
  "prep_time_minutes": integer,
  "cook_time_minutes": integer,
  "servings": integer
}"""


async def generate_recipe(
    ingredients: list[str],
    dietary_preferences: list[str] = None,
) -> dict:
    dietary_note = ""
    if dietary_preferences:
        dietary_note = f" The recipe must be suitable for: {', '.join(dietary_preferences)}."

    user_message = (
        f"Generate a recipe using these available ingredients: "
        f"{', '.join(ingredients)}.{dietary_note}"
    )

    prompt = f"<|system|>\n{SYSTEM_PROMPT}\n<|user|>\n{user_message}\n<|assistant|>\n"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api-inference.huggingface.co/models/HuggingFaceH4/zephyr-7b-beta",
            headers={"Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 600,
                    "temperature": 0.7,
                    "return_full_text": False,
                },
            },
        )

    if response.status_code != 200:
        raise RuntimeError(f"HF API error: {response.status_code}")

    raw = response.json()
    generated_text = (
        raw[0]["generated_text"] if isinstance(raw, list)
        else raw.get("generated_text", "")
    )

    # Strip markdown fences if model adds them despite instructions
    generated_text = generated_text.strip()
    if generated_text.startswith("```"):
        generated_text = generated_text.split("```")[1]
        if generated_text.startswith("json"):
            generated_text = generated_text[4:]

    return json.loads(generated_text.strip())

async def analyze_nutrition(
    ingredients: list[str],
    servings: int = 1,
) -> dict:
    ingredient_text = "\n".join(f"- {i}" for i in ingredients)
    prompt = f"""Estimate the total nutritional content for {servings} serving(s) 
of a dish made with:
{ingredient_text}

Respond ONLY with a JSON object with these exact keys:
{{"calories": number, "protein_g": number, "carbohydrates_g": number, 
"fat_g": number, "fiber_g": number}}"""

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
            headers={"Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 150,
                    "return_full_text": False,
                },
            },
        )

    if response.status_code != 200:
        raise RuntimeError(f"HF API error: {response.status_code}")

    raw = response.json()
    text = raw[0]["generated_text"] if isinstance(raw, list) else raw.get("generated_text", "")
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    nutrition = json.loads(text.strip())
    nutrition["disclaimer"] = "Values are AI-estimated and not clinically verified."
    return nutrition

SUBSTITUTE_CANDIDATES = [
    "butter", "olive oil", "coconut oil", "vegetable oil",
    "milk", "oat milk", "almond milk", "soy milk", "coconut milk",
    "eggs", "flax eggs", "aquafaba",
    "all-purpose flour", "almond flour", "oat flour", "rice flour",
    "sugar", "honey", "maple syrup", "agave",
    "beef", "chicken", "tofu", "tempeh", "lentils", "jackfruit",
    "cheese", "nutritional yeast", "cashew cheese",
    "cream", "coconut cream", "silken tofu",
    "pancetta", "turkey bacon", "smoked tempeh", "mushroom bacon",
    "sour cream", "greek yogurt", "coconut yogurt",
    "soy sauce", "tamari", "coconut aminos",
    "mayonnaise", "avocado", "hummus",
]


async def get_substitutions(
    ingredient: str,
    restriction: str = None,
) -> dict:
    import torch
    model, processor = _load_clip()

    source_inputs = processor(
        text=[ingredient], return_tensors="pt", padding=True
    )
    with torch.no_grad():
        source_emb = model.get_text_features(**source_inputs)
        source_emb = source_emb / source_emb.norm(dim=-1, keepdim=True)

    cand_inputs = processor(
        text=SUBSTITUTE_CANDIDATES, return_tensors="pt", padding=True
    )
    with torch.no_grad():
        cand_embs = model.get_text_features(**cand_inputs)
        cand_embs = cand_embs / cand_embs.norm(dim=-1, keepdim=True)

    similarities = (source_emb @ cand_embs.T).squeeze(0).tolist()
    scored = sorted(
        zip(SUBSTITUTE_CANDIDATES, similarities),
        key=lambda x: x[1],
        reverse=True,
    )

    top = [
        (s, score) for s, score in scored
        if s.lower() != ingredient.lower()
    ][:5]

    explanations = await _explain_substitutions(
        ingredient, [s for s, _ in top], restriction
    )

    return {
        "original_ingredient": ingredient,
        "restriction": restriction,
        "substitutions": [
            {
                "substitute": sub,
                "similarity_score": round(score, 3),
                "effect": explanations.get(
                    sub, "A suitable alternative for this ingredient."
                ),
            }
            for sub, score in top
        ],
    }


async def _explain_substitutions(
    original: str,
    substitutes: list[str],
    restriction: str = None,
) -> dict:
    restriction_note = (
        f" (dietary restriction: {restriction})" if restriction else ""
    )
    subs_list = ", ".join(substitutes)

    prompt = f"""For cooking, briefly explain how each substitute affects taste 
and texture when replacing "{original}"{restriction_note}.
Substitutes: {subs_list}
Respond ONLY with a JSON object mapping each substitute name to a 1-2 sentence explanation.
Example: {{"olive oil": "Lighter flavour. Works well in most savoury dishes."}}"""

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
            headers={"Authorization": f"Bearer {settings.HUGGINGFACE_API_KEY}"},
            json={
                "inputs": prompt,
                "parameters": {
                    "max_new_tokens": 300,
                    "return_full_text": False,
                },
            },
        )

    if response.status_code != 200:
        return {}

    raw = response.json()
    text = raw[0]["generated_text"] if isinstance(raw, list) else raw.get("generated_text", "")
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text.strip())
    except Exception:
        return {}