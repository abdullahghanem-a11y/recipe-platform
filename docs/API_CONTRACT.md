# API Contract — AI-Powered Recipe Sharing Platform

Version: 1.0  
Base URL: `https://your-app.onrender.com`  
Auth: Community endpoints require `Authorization: Bearer <jwt_token>`. AI endpoints require `X-API-Key: <api_key>`.

---

## Data models

### User
```json
{
  "id": "uuid",
  "username": "string",
  "email": "string",
  "dietary_preferences": ["vegan", "gluten-free"],
  "created_at": "ISO 8601 timestamp"
}
```

### Recipe
```json
{
  "id": "uuid",
  "user_id": "uuid",
  "title": "string",
  "description": "string",
  "ingredients": [
    { "name": "string", "quantity": "string", "unit": "string" }
  ],
  "steps": ["string"],
  "cuisine_type": "string",
  "prep_time_minutes": "integer",
  "cook_time_minutes": "integer",
  "servings": "integer",
  "average_rating": "float | null",
  "created_at": "ISO 8601 timestamp"
}
```

### Inference log
```json
{
  "id": "uuid",
  "user_id": "uuid",
  "endpoint": "string",
  "processing_ms": "integer",
  "created_at": "ISO 8601 timestamp"
}
```

---

## Community layer

### Auth

#### POST /auth/register
Create a new user account.

Request body:
```json
{
  "username": "string (required, 3–30 chars)",
  "email": "string (required, valid email)",
  "password": "string (required, min 8 chars)",
  "dietary_preferences": ["string"] 
}
```

Response `201`:
```json
{
  "user": { ...User },
  "access_token": "string (JWT)",
  "token_type": "bearer"
}
```

Errors: `400` invalid fields, `409` email/username already taken.

---

#### POST /auth/login
Authenticate and receive a JWT token.

Request body:
```json
{
  "email": "string",
  "password": "string"
}
```

Response `200`:
```json
{
  "access_token": "string (JWT)",
  "token_type": "bearer"
}
```

Errors: `401` invalid credentials.

---

### Users

#### GET /users/me
Get the current authenticated user's profile.

Auth: JWT required.

Response `200`:
```json
{ ...User }
```

---

#### PATCH /users/me
Update dietary preferences or username.

Auth: JWT required.

Request body (all fields optional):
```json
{
  "username": "string",
  "dietary_preferences": ["string"]
}
```

Response `200`:
```json
{ ...User }
```

---

### Recipes

#### POST /recipes
Create a new recipe.

Auth: JWT required.

Request body:
```json
{
  "title": "string (required)",
  "description": "string",
  "ingredients": [
    { "name": "string", "quantity": "string", "unit": "string" }
  ],
  "steps": ["string (max 30 steps)"],
  "cuisine_type": "string",
  "prep_time_minutes": "integer",
  "cook_time_minutes": "integer",
  "servings": "integer"
}
```

Response `201`:
```json
{ ...Recipe }
```

Errors: `400` validation failure (e.g. steps exceed 30), `401` not authenticated.

---

#### GET /recipes
Search and filter recipes.

Auth: Not required.

Query parameters:
- `ingredient` — filter recipes containing this ingredient (string, repeatable)
- `cuisine` — filter by cuisine type (string)
- `dietary` — filter by dietary restriction (string, e.g. `vegan`)
- `max_prep_time` — filter by max prep time in minutes (integer)
- `sort` — `newest` | `top_rated` (default: `newest`)
- `page` — page number (default: 1)
- `limit` — results per page (default: 20, max: 50)

Response `200`:
```json
{
  "results": [{ ...Recipe }],
  "total": "integer",
  "page": "integer",
  "limit": "integer"
}
```

---

#### GET /recipes/{id}
Get a single recipe by ID.

Response `200`: `{ ...Recipe }`

Errors: `404` recipe not found.

---

#### PATCH /recipes/{id}
Update a recipe. Only the recipe's author can update it.

Auth: JWT required.

Request body: Same as POST /recipes (all fields optional).

Response `200`: `{ ...Recipe }`

Errors: `403` not the author, `404` not found.

---

#### DELETE /recipes/{id}
Delete a recipe. Only the recipe's author can delete it.

Auth: JWT required.

Response `204`: No content.

Errors: `403` not the author, `404` not found.

---

### Ratings

#### POST /recipes/{id}/ratings
Rate a recipe. A user can only rate each recipe once (subsequent calls update the existing rating).

Auth: JWT required.

Request body:
```json
{ "score": "integer (1–5)" }
```

Response `200`:
```json
{
  "recipe_id": "uuid",
  "user_id": "uuid",
  "score": "integer",
  "recipe_average_rating": "float"
}
```

---

### Saved recipes and collections

#### POST /recipes/{id}/save
Save a recipe. Optionally assign it to a collection.

Auth: JWT required.

Request body:
```json
{ "collection_id": "uuid (optional)" }
```

Response `200`:
```json
{ "saved": true, "recipe_id": "uuid", "collection_id": "uuid | null" }
```

---

#### GET /users/me/saved
Get all recipes saved by the current user.

Auth: JWT required.

Query parameters: `collection_id` (optional), `page`, `limit`.

Response `200`:
```json
{
  "results": [{ ...Recipe }],
  "total": "integer"
}
```

---

#### POST /collections
Create a new recipe collection.

Auth: JWT required.

Request body:
```json
{ "name": "string (required)" }
```

Response `201`:
```json
{ "id": "uuid", "name": "string", "user_id": "uuid", "created_at": "timestamp" }
```

---

#### GET /collections
Get all collections belonging to the current user.

Auth: JWT required.

Response `200`:
```json
[{ "id": "uuid", "name": "string", "recipe_count": "integer" }]
```

---

## AI Intelligence Layer

All `/ai/*` endpoints require `X-API-Key: <key>` header.  
All endpoints are rate-limited (see DECISIONS.md, section 5).  
Cache: identical inputs return cached results within 24 hours without re-running inference.

---

### POST /ai/recognize

Identify a dish from a food photo.

Content type: `multipart/form-data`

Request fields:
- `image` (file, required) — JPEG or PNG, max 10MB

Request flow:
1. Validate image format and size
2. Run ViT food confidence check. If max confidence < 0.3, reject with 422.
3. Run full classification, return top-3 predictions
4. Query the platform recipe database for matching dishes

Response `200`:
```json
{
  "identified_dish": "pasta carbonara",
  "confidence": 0.91,
  "top_3": [
    { "label": "pasta carbonara", "score": 0.91 },
    { "label": "spaghetti", "score": 0.06 },
    { "label": "fettuccine alfredo", "score": 0.02 }
  ],
  "matching_recipes": [
    { "recipe_id": "uuid", "title": "Classic Carbonara", "average_rating": 4.7 }
  ]
}
```

Errors:
- `415` unsupported image format
- `422` image confidence below food threshold — `{ "error": "not_food", "message": "Image does not appear to contain food." }`

---

### POST /ai/generate

Generate a structured recipe from available ingredients.

Content type: `application/json`

Request body:
```json
{
  "ingredients": ["string (max 20 items)"],
  "dietary_preferences": ["string (optional)"]
}
```

Validation: `ingredients` must have 1–20 items.

Response `200`: A complete Recipe object (same schema as community recipes, suitable for saving directly via `POST /recipes`):
```json
{
  "title": "Quick Pasta Carbonara",
  "description": "string",
  "ingredients": [{ "name": "string", "quantity": "string", "unit": "string" }],
  "steps": ["string"],
  "cuisine_type": "Italian",
  "prep_time_minutes": 10,
  "cook_time_minutes": 20,
  "servings": 2
}
```

Errors:
- `422` more than 20 ingredients
- `503` generation service unavailable (Hugging Face API down) — includes `retry_after_seconds`

---

### POST /ai/nutrition

Estimate nutritional breakdown for a recipe.

Content type: `application/json`

Request body:
```json
{
  "ingredients": ["string (max 20 items)"],
  "servings": "integer (default 1)"
}
```

Response `200`:
```json
{
  "per_serving": {
    "calories": 620,
    "protein_g": 28,
    "carbohydrates_g": 74,
    "fat_g": 22,
    "fiber_g": 3
  },
  "disclaimer": "Values are AI-estimated and not clinically verified."
}
```

Note: The `disclaimer` field is always present and non-optional.

---

### POST /ai/substitute

Get substitution suggestions for an ingredient.

Content type: `application/json`

Request body:
```json
{
  "ingredient": "string (required)",
  "restriction": "string (optional, e.g. vegan, gluten-free, lactose-free, out-of-stock)"
}
```

Response `200`:
```json
{
  "original_ingredient": "pancetta",
  "restriction": "vegan",
  "substitutions": [
    {
      "substitute": "smoked tempeh",
      "similarity_score": 0.84,
      "effect": "Provides a similar smoky, savory flavor. Texture is slightly firmer and less fatty. Works well in pasta and rice dishes."
    },
    {
      "substitute": "mushroom bacon",
      "similarity_score": 0.79,
      "effect": "Umami-forward with a chewy texture. Less salty than pancetta. Good in egg-based dishes."
    }
  ]
}
```

---

### POST /ai/assist

Unified smart cooking assistant. Accepts a photo, an ingredient list, or both.

Content type: `multipart/form-data`

Request fields:
- `image` (file, optional) — JPEG or PNG
- `ingredients` (JSON array as string, optional) — max 20 items

At least one of `image` or `ingredients` must be provided. If neither is provided, returns `422`.

Response `200` — shape adapts to the input provided:
```json
{
  "input_type": "photo_and_ingredients | photo_only | ingredients_only",
  "dish_recognition": {
    "identified_dish": "string",
    "confidence": 0.91,
    "top_3": [...],
    "matching_recipes": [...]
  },
  "generated_recipe": { ...Recipe },
  "nutrition_per_serving": {
    "calories": 620,
    "protein_g": 28,
    "carbohydrates_g": 74,
    "fat_g": 22,
    "fiber_g": 3,
    "disclaimer": "Values are AI-estimated and not clinically verified."
  },
  "suggested_substitutions": [
    {
      "original": "string",
      "substitute": "string",
      "reason": "string"
    }
  ],
  "processing_time_ms": {
    "recognition": 380,
    "generation": 1240,
    "nutrition": 590,
    "substitutions": 430,
    "total": 2640
  }
}
```

Field omission rules:
- `dish_recognition` is omitted if no image was provided
- `generated_recipe` is omitted if no ingredients were provided
- If a sub-task fails (e.g. generation service unavailable), that field is replaced with `{ "error": "string", "message": "string" }` — other successful tasks still appear

---

## Error response format

All errors follow this shape:
```json
{
  "error": "snake_case_error_code",
  "message": "Human-readable description of the problem.",
  "details": { }
}
```

Common HTTP status codes:
- `400` Bad request (missing or malformed fields)
- `401` Unauthenticated (missing or invalid JWT)
- `403` Forbidden (authenticated but not authorized)
- `404` Resource not found
- `409` Conflict (duplicate resource)
- `415` Unsupported media type
- `422` Validation error (valid format, but business rule violated)
- `429` Rate limit exceeded
- `503` Service unavailable (downstream AI service down)
