# Jev API, as observed

Ground truth for this repo.

Observed 2026-09-20 (IST) from Bengaluru with `spike.py`, raw `httpx`, no SDK.
Response `model` field: `jev-1.13.0`. Docs cross-checked at https://docs.typesafe.ai/api.md
and https://docs.typesafe.ai/models.md the same day.

## Endpoint

```
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <TYPESAFE_API_KEY>
Content-Type: application/json
```

## Request shape (confirmed, 200)

Questions are declared **flat with a `type` discriminator**. The nested form
`{"noul": {"instructions": ...}}` is rejected with 422 (see Errors).

```json
{
  "state": {"document_kind": "commercial contract", "sentence": "..."},
  "model": "jev-1.13.0",
  "questions": {
    "obligation":  {"type": "noul",   "instructions": "..."},
    "risk":        {"type": "score",  "instructions": "...", "criteria": ["level 0", "level 1", "level 2"]},
    "clause_type": {"type": "choice", "instructions": "...", "criteria": {"opt_a": "desc", "opt_b": null}}
  }
}
```

- `state`: string, object, or array. Object recommended.
- `model`: `jev-1.13.0` pinned. `jev-latest` and `jev-preview` both alias to it today.
- Noul: `criteria` optional, `{"true": "...", "false": "..."}`.
- Score: `criteria` is an ordered array, minimum 2 levels. Docs list no maximum; SPEC says 10.
- Choice: `criteria` is a map option -> description (or null). Docs list no maximum; SPEC says 255.
- Question keys are not shown to the model.
- Limits from docs: 64k tokens per request total, 32k for state plus the longest question.

## Response shape (confirmed)

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "obligation":  {"type": "noul", "noul": 0.31},
    "risk":        {"type": "score", "score": 0.85, "confidence": 0.63,
                    "legend": {"0": "Pure boilerplate", "1": "Standard market terms", "2": "...", "3": "..."},
                    "probabilities": {"0": 0.26, "1": 0.63, "2": 0.11, "3": 0.0}},
    "clause_type": {"type": "choice", "choice": "termination", "confidence": 1.0,
                    "probabilities": {"boilerplate": 0.0, "other": 0.0, "payment": 0.0, "termination": 1.0}}
  },
  "usage": {"input_tokens": 458, "output_tokens": 80}
}
```

Paths that matter:

| Primitive | Value path                  | Distribution path       | Confidence          |
| --------- | --------------------------- | ----------------------- | ------------------- |
| Noul      | `answers[k].noul`           | none                    | none                |
| Score     | `answers[k].score` (float)  | `answers[k].probabilities` (keys `"0".."n-1"`), plus `legend` | `answers[k].confidence` |
| Choice    | `answers[k].choice`         | `answers[k].probabilities` (keys = option names) | `answers[k].confidence` |

Probabilities are rounded to 2 decimals server-side. Score `score` is the probability-weighted
level index, so it lives in `[0, n_levels - 1]`.

## Response headers

```
server: istio-envoy
x-typesafe-request-id: req_01a0bbc755a67c9b9d90b907f8b3bb71
x-envoy-upstream-service-time: 249
content-type: application/json
```

No rate-limit headers were present on 200s. `x-envoy-upstream-service-time` is server-side
milliseconds, which lets us separate inference time from Pacific round trip.

## Errors (observed)

| Status | Trigger | Body |
| ------ | ------- | ---- |
| 422 | nested question shape | `{"detail":[{"type":"union_tag_not_found","loc":["body","questions","q"],"msg":"Unable to extract tag using discriminator 'type'", ...}]}` |
| 400 | unknown model | `{"detail":{"error_type":"api_usage_error","message":"Unknown model: jev-9.9.9"}}` |
| 401 | bad key | `{"detail":{"error_type":"authentication_error","message":"Cannot authenticate with the server. ..."}}` |
| 429 | rate limit (docs) | retry with backoff, honor `retry-after` if present |
| 529 | overloaded (docs) | retry with backoff |

422 bodies are FastAPI/pydantic style: `detail` is a list. 400/401 bodies: `detail` is an object.
Error handling must accept both.

## Latency from Bengaluru (20 calls, warm keep-alive connection, 3 questions, 458 input tokens)

| cold first call | p50 | p95 | min | max |
| --------------- | --- | --- | --- | --- |
| 1134 ms | 334 ms | 417 ms | 293 ms | 418 ms |

Server-side time on the first call was 249 ms, so roughly 85 ms of the warm number is network.
The 1.1 s cold call is the TLS handshake to US-West. Connection reuse is mandatory.

## Pricing (docs, 2026-09-20)

$0.042 per million input tokens. Output tokens free. Rate limits 250k tokens/s and
1,200 requests/min, described as subject to change without notice.
