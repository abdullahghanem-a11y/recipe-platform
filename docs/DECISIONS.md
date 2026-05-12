# DECISIONS.md — AI-Powered Recipe Sharing Platform

This document justifies every major architectural and design decision in the platform.
It is a required Phase 1 deliverable and will be referenced during the oral defense.

---

## 1. Model choices

### 1.1 Dish recognition — `google/vit-base-patch16-224`

**What it does:** Vision Transformer (ViT) classifies an uploaded food photo into a food category. It returns the top predicted label along with a confidence score.

**Why this model:** ViT was pre-trained on ImageNet-21k and fine-tuned on food datasets. It runs efficiently on CPU, produces reliable top-k predictions, and its output (a label + softmax confidence scores) maps cleanly to our required response shape — `identified_dish`, `confidence`, `top_3`.

**Why not a heavier model:** We are running this locally inside Docker on CPU. ViT-base (86M parameters) strikes the right balance between accuracy and inference speed (~300–500ms per image on CPU). Larger models (e.g. ViT-Large) would be too slow for a synchronous API endpoint without GPU.

**Image pre-screening gate:** In Phase 3, this same model serves as a validation gate. If the top food-category confidence for any submitted image is below 0.3, the request is rejected with a 422 error. This prevents off-topic images from wasting downstream inference time and protects the quality of dish recognition results.

---

### 1.2 Recipe generation — `HuggingFaceH4/zephyr-7b-beta` (via Hugging Face Inference API)

**What it does:** Given a list of available ingredients, generates a fully structured recipe in JSON format including title, ingredients with quantities, step-by-step instructions, estimated prep/cook time, and serving size.

**Why this model:** Zephyr-7b-beta is an instruction-tuned LLM fine-tuned from Mistral-7B. It follows structured output instructions reliably when prompted correctly, producing parseable JSON rather than free-form prose. Its instruction format (system prompt + `<|user|>` / `<|assistant|>` tags) gives us precise control over output structure.

**Why hosted API instead of local:** At 7 billion parameters, this model requires ~14GB of GPU memory for efficient inference. Running it on CPU locally would take 60–120 seconds per request — unacceptable for a user-facing API. We offload generation to the Hugging Face Inference API, which provides hosted GPU inference. This mirrors how real production systems are built: lightweight models run locally for speed and cost, while heavy generation is offloaded to managed endpoints.

**Graceful degradation:** If the Hugging Face Inference API is unavailable (network error, rate limit, timeout), the `/ai/generate` and `/ai/assist` endpoints return a structured error response:
```json
{
  "error": "generation_unavailable",
  "message": "Recipe generation service is temporarily unavailable. Please try again shortly.",
  "retry_after_seconds": 30
}
```
The API does not crash or return a 500. Other sub-tasks in `/ai/assist` that do not depend on generation still run and return results.

---

### 1.3 Nutritional analysis — `llmware-ai/slim-extract-tool`

**What it does:** Given an ingredient list as text (e.g. "200g chicken breast, 1 cup rice, 2 tbsp olive oil"), extracts and estimates nutritional values per serving: calories, protein, carbohydrates, fat, and fiber.

**Why this model:** `slim-extract-tool` is a compact instruction-following extraction model designed specifically for pulling structured values out of unstructured text. It runs locally on CPU in under 1 second and produces clean key-value output that can be parsed into our required nutrition schema.

**Disclaimer field — rationale:** Every response from `/ai/nutrition` includes:
```json
"disclaimer": "Values are AI-estimated and not clinically verified."
```
This is a deliberate, legally aware design decision. Nutritional values computed from ingredient descriptions are approximations — they do not account for cooking losses, measurement variation, or ingredient brand differences. Presenting these values without a disclaimer could mislead users with specific dietary medical needs. The disclaimer field is non-optional and cannot be stripped from the response.

---

### 1.4 Ingredient substitution — `openai/clip-vit-base-patch32` + text generation

**What it does:** Given an ingredient and an optional restriction (e.g. "butter, vegan"), returns a list of substitutions with explanations of how each affects taste and texture.

**Why CLIP for similarity:** CLIP (Contrastive Language-Image Pretraining) creates embeddings that place semantically similar concepts near each other in vector space. We encode the source ingredient as a text embedding, then compare it against a curated list of potential substitute ingredients to find the closest semantic matches. This produces more contextually appropriate substitutions than a simple keyword lookup — for example, "pancetta" retrieves "turkey bacon" and "smoked tempeh" rather than unrelated pork products.

**Why text generation for explanations:** CLIP gives us *which* substitutes are similar, but not *why* they work or how they change the dish. We pass the top CLIP matches to a text generation call to produce the brief explanation for each substitution.

**Substitution logic flow:**
1. Encode the source ingredient with CLIP
2. Encode a curated substitute candidate list with CLIP
3. Compute cosine similarity, filter by dietary restriction tag if provided
4. Return top 3-5 substitutes
5. For each, call the text generation model to produce a 1-2 sentence taste/texture explanation

---

## 2. Architecture decisions

### 2.1 Hybrid local CPU + hosted API architecture

Three of the four models (ViT, CLIP, slim-extract-tool) run locally inside the Docker container on CPU. The text generation model (Zephyr-7b-beta) is called via the Hugging Face Inference API.

**Why hybrid:** Running all four models locally would require a GPU-equipped host for acceptable response times. The hosted API option for Zephyr makes the stack deployable on standard cloud instances (e.g. Render free/starter tier) without GPU hardware, while keeping the three lightweight models local for low-latency, zero-API-cost inference.

**Trade-off acknowledged:** The hosted API introduces a network dependency. Zephyr endpoints can be slow (~5–15s) or temporarily unavailable. This is handled with timeout configuration and the graceful degradation pattern described in section 1.2.

---

### 2.2 Redis caching (24-hour TTL for inference inputs)

**What is cached:** The hash of each inference input (e.g. SHA-256 of the ingredient list string) is stored as a Redis key with the full inference result as the value.

**TTL:** 24 hours. After 24 hours the cache entry expires and the next identical request re-runs inference.

**Why 24 hours:** Inference results for the same input do not change meaningfully within a day. A user who sends "chicken, rice, garlic" at 9am and again at 11am should receive the same generated recipe without paying the latency and API cost of two model calls. A 24-hour window balances freshness with cost savings.

**What is not cached:** The `/ai/recognize` endpoint accepts images. Images are hashed (SHA-256 of raw bytes) and cached the same way. User-specific state (authentication, profile) is never part of the cache key.

**Cache miss flow:**
1. Hash the input
2. Check Redis: `GET cache:{hash}`
3. If hit: return cached result, skip inference
4. If miss: run inference, write result to Redis with 24h TTL, log to `inference_logs`

---

### 2.3 PostgreSQL for all persistent data

User data, recipe data, ratings, collections, and inference logs all live in a single PostgreSQL instance. No separate NoSQL store.

**Why not MongoDB for recipes:** Recipes have a well-defined, consistent structure. Using a relational database gives us foreign key constraints (a rating must reference a real recipe), easy `JOIN` queries (get all recipes saved by user X with their average rating), and `ARRAY` + `JSONB` columns for the few fields that genuinely benefit from flexible structure (ingredients, steps, dietary preferences).

---

### 2.4 JWT for community endpoints, API key for AI endpoints

Community layer endpoints (`/users`, `/recipes`, `/ratings`, `/collections`) use JWT (JSON Web Token) authentication. A user logs in and receives a short-lived access token they send with each request.

AI endpoints (`/ai/*`) use API key authentication via the `X-API-Key` request header.

**Why different schemes:** The AI endpoints are designed to be consumed by third-party cooking applications, not just by end users in a browser session. API keys are simpler for machine-to-machine integrations and can be scoped, rotated, and rate-limited per client application independently of user sessions.

---

### 2.5 Rate limiting on AI endpoints

All `/ai/*` endpoints are rate-limited because text generation is computationally expensive (API cost + latency). Default limits:

| Endpoint | Limit |
|---|---|
| `/ai/recognize` | 30 requests / minute per API key |
| `/ai/generate` | 10 requests / minute per API key |
| `/ai/nutrition` | 30 requests / minute per API key |
| `/ai/substitute` | 30 requests / minute per API key |
| `/ai/assist` | 5 requests / minute per API key |

`/ai/assist` has the tightest limit because it chains multiple models and can consume significant resources in a single request.

---

## 3. Prompt engineering strategy (for `/ai/generate`)

Getting Zephyr-7b-beta to reliably return structured JSON requires three techniques:

**3.1 System prompt with explicit schema**

```
You are a recipe generation assistant. You must ONLY respond with a valid JSON object.
Do not include any explanation, preamble, or markdown formatting.
The JSON must follow this exact schema:
{
  "title": string,
  "description": string,
  "ingredients": [{"name": string, "quantity": string, "unit": string}],
  "steps": [string],
  "prep_time_minutes": integer,
  "cook_time_minutes": integer,
  "servings": integer,
  "cuisine_type": string
}
```

**3.2 Few-shot example in the prompt**

Before the user's ingredient list, we include one complete example input/output pair so the model calibrates on the expected format.

**3.3 Output parsing with fallback**

Model output is parsed with `json.loads()`. If parsing fails (model hallucinated extra text, incomplete JSON, markdown fences), we strip common wrappers (` ```json ... ``` `) and retry parsing. If it still fails, we return a structured error to the client rather than crashing.

---

## 4. Conditional pipeline logic in `/ai/assist`

The unified endpoint accepts `photo` (file upload), `ingredients` (list), or both.

| Input provided | Tasks that run |
|---|---|
| Photo only | recognition |
| Ingredients only | generation, nutrition, substitution |
| Photo + ingredients | recognition, generation, nutrition, substitution |
| Neither | 422 error — at least one input required |

Each task runs independently. If one task fails (e.g. generation API is down), the others still complete and their results are included in the response. The failing task's field is replaced with an error object rather than omitting the field silently, so the caller always knows what succeeded and what didn't.

**Processing time tracking:** Each sub-task is timed with `time.perf_counter()` before and after the model call. Times are reported in the `processing_time_ms` block of the response.

---

## 5. Input validation rules (Phase 3)

| Rule | Reason |
|---|---|
| Max 20 ingredients per request | Prevents prompt injection via oversized inputs; keeps generation prompts within context window |
| Max 30 steps per recipe | Keeps stored recipes navigable and consistent |
| Image confidence gate (< 0.3 = reject) | Prevents non-food images from being processed by downstream models |
| Image must be JPEG or PNG | Limits attack surface; ViT is optimized for these formats |
| Nutritional disclaimer is non-strippable | Legal and ethical requirement |
