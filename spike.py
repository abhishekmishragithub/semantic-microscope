"""Milestone 0: one raw POST to Jev, then 20 timed repeats. No abstractions on purpose."""

import json
import os
import statistics
import sys
import time

import httpx

URL, MODEL = "https://api.typesafe.ai/v1/systemone", "jev-1.13.0"
STATE = {
    "document_kind": "commercial contract",
    "sentence": "Either party may terminate this Agreement upon thirty (30) days written notice.",
}
QUESTIONS_A = {  # shape A: {"type": ..., "instructions": ...}
    "obligation": {
        "type": "noul",
        "instructions": "This sentence creates a binding obligation on one of the parties.",
    },
    "risk": {
        "type": "score",
        "instructions": "How legally load-bearing and unusual is this sentence?",
        "criteria": [
            "Pure boilerplate",
            "Standard market terms",
            "Materially binding",
            "Unusual or one-sided",
        ],
    },
    "clause_type": {
        "type": "choice",
        "instructions": "Which category does this sentence most belong to?",
        "criteria": {
            "payment": "Fees or payment timing",
            "termination": "Ending the agreement",
            "boilerplate": "Notices, governing law",
            "other": "None of the above",
        },
    },
}
QUESTIONS_B = {
    k: {v["type"]: {kk: vv for kk, vv in v.items() if kk != "type"}} for k, v in QUESTIONS_A.items()
}


def post(client: httpx.Client, questions: dict) -> tuple[httpx.Response, float]:
    body = {"state": STATE, "model": MODEL, "questions": questions}
    t0 = time.perf_counter()
    r = client.post(URL, json=body)
    return r, (time.perf_counter() - t0) * 1000


def main() -> None:
    key = os.environ.get("TYPESAFE_API_KEY") or sys.exit("TYPESAFE_API_KEY not set")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    with httpx.Client(headers=headers, timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        for label, questions in (
            ("A: {type, instructions}", QUESTIONS_A),
            ("B: {noul: {instructions}}", QUESTIONS_B),
        ):
            print(f"\n=== Trying shape {label} ===")
            print(
                "REQUEST:",
                json.dumps({"state": STATE, "model": MODEL, "questions": questions}, indent=2),
            )
            r, ms = post(client, questions)
            print(f"STATUS: {r.status_code}   LATENCY: {ms:.0f} ms")
            print("HEADERS:", json.dumps(dict(r.headers), indent=2))
            print(
                "BODY:",
                json.dumps(r.json(), indent=2)
                if "json" in r.headers.get("content-type", "")
                else r.text,
            )
            if r.status_code == 200:
                break
        else:
            sys.exit("Neither shape returned 200.")
        lat = []
        for _ in range(20):
            r, ms = post(client, questions)
            lat.append(ms)
            print(f"  {r.status_code} {ms:.0f}ms model={r.json().get('model')}", flush=True)
        lat.sort()
        q = statistics.quantiles(lat, n=100)
        print(
            f"\n20 calls, warm connection: p50={q[49]:.0f}ms p95={q[94]:.0f}ms min={lat[0]:.0f}ms max={lat[-1]:.0f}ms"
        )


if __name__ == "__main__":
    main()
