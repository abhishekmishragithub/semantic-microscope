"""Human-label calibration check for one Noul dimension.

The prediction is never printed while labelling. Seeing Jev's number first would measure the
human's anchoring, not the model's calibration. Labels are saved after every answer so a run can
be interrupted and the table re-printed later without re-labelling.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from microscope.stats import load_run

BUCKETS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]


def bucket_of(p: float) -> int:
    return min(4, int(p * 5))


def candidates(recs: list[dict[str, Any]], dimension: str) -> list[tuple[int, float, str]]:
    out = []
    for r in recs:
        a = (r.get("answers") or {}).get(dimension)
        if a and a.get("kind") == "noul":
            out.append((r["idx"], float(a["value"]), r["text"]))
    return out


def stratified_sample(
    cands: list[tuple[int, float, str]], n: int, seed: int, exclude: set[int] | None = None
) -> list[int]:
    """Roughly n/5 from each bucket; short buckets give their unused quota to the others.
    `exclude` keeps already-sampled sentences out so a second batch is all fresh."""
    rng = random.Random(seed)
    pools: list[list[int]] = [[] for _ in BUCKETS]
    for idx, p, _ in cands:
        if exclude and idx in exclude:
            continue
        pools[bucket_of(p)].append(idx)
    for pool in pools:
        rng.shuffle(pool)
    quota = [n // 5 + (1 if i < n % 5 else 0) for i in range(5)]
    chosen: list[int] = []
    for i, pool in enumerate(pools):
        take = min(quota[i], len(pool))
        chosen += pool[:take]
        del pool[:take]
    leftover = n - len(chosen)
    while leftover > 0 and any(pools):
        for pool in pools:
            if pool and leftover > 0:
                chosen.append(pool.pop())
                leftover -= 1
    order = chosen[:]
    rng.shuffle(order)
    return order


def labels_path(data_dir: Path, run_id: str) -> Path:
    return data_dir / f"{run_id}.labels.json"


def load_labels(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text())
    return {"labels": {}}


def save_labels(path: Path, doc: dict[str, Any]) -> None:
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))


def ask(prompt: str) -> str | None:
    """Returns 'y', 'n', 's' or None on quit/EOF."""
    while True:
        try:
            raw = input(prompt).strip().lower()
        except EOFError:
            return None
        if raw in ("q", "quit"):
            return None
        if raw in ("y", "yes"):
            return "y"
        if raw in ("n", "no"):
            return "n"
        if raw in ("s", "skip", ""):
            return "s"
        print("  answer y, n, s (skip) or q (quit)")


def label_interactively(
    doc: dict[str, Any],
    sample: list[int],
    by_idx: dict[int, tuple[float, str]],
    instructions: str,
    path: Path,
) -> None:
    todo = [i for i in sample if str(i) not in doc["labels"]]
    if not todo:
        return
    print(f"\nQuestion: {instructions}")
    print(
        f"{len(todo)} sentences to label. Jev's prediction is hidden. y = yes, n = no, s = skip, q = quit.\n"
    )
    for k, idx in enumerate(todo, 1):
        p, text = by_idx[idx]
        print(f"[{k}/{len(todo)}]  #{idx}\n  {text}\n")
        ans = ask("  y/n/s > ")
        if ans is None:
            print("\nstopped; progress saved")
            break
        doc["labels"][str(idx)] = {
            "human": None if ans == "s" else ans == "y",
            "p": p,
            "labelled_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        save_labels(path, doc)
        print()


def calibration_table(doc: dict[str, Any]) -> str:
    rows = [(v["p"], v["human"]) for v in doc["labels"].values() if v["human"] is not None]
    skipped = sum(1 for v in doc["labels"].values() if v["human"] is None)
    if not rows:
        return "no labels yet"
    lines = [f"{'bucket':<10}{'n':>4}   {'predicted':>9}   {'observed':>8}"]
    for lo, hi in BUCKETS:
        b = [(p, y) for p, y in rows if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if not b:
            lines.append(f"{lo:.1f}–{hi:.1f}   {0:>4}   {'-':>9}   {'-':>8}")
            continue
        pred = sum(p for p, _ in b) / len(b)
        obs = sum(1 for _, y in b if y) / len(b)
        lines.append(f"{lo:.1f}–{hi:.1f}   {len(b):>4}   {pred:>9.2f}   {obs:>8.2f}")
    brier = sum((p - (1.0 if y else 0.0)) ** 2 for p, y in rows) / len(rows)
    lines.append("")
    lines.append(f"Brier score: {brier:.3f}   (lower is better; 0.25 = always guessing 0.5)")
    lines.append(f"n labelled: {len(rows)}   skipped: {skipped}")
    return "\n".join(lines)


def run_validate(
    data_dir: Path,
    run_id: str,
    dimension: str,
    *,
    n: int = 30,
    seed: int | None = None,
    report_only: bool = False,
    add: int = 0,
) -> str:
    """First run: draw n sentences (seed defaults to 42) and store the sample in the labels
    file. Later runs continue that same sample; --seed has no effect on a stored sample.
    `add` draws that many fresh sentences (never ones already sampled), appends them to the
    stored sample, and records the batch. Existing labels are never touched."""
    recs, meta = load_run(data_dir, run_id)
    dims = ((meta.get("questions") or {}).get("dimensions")) or {}
    spec = dims.get(dimension)
    if spec is not None and spec.get("type") != "noul":
        raise ValueError(f"dimension {dimension!r} is a {spec.get('type')}; validate needs a noul")
    cands = candidates(recs, dimension)
    if not cands:
        raise ValueError(f"no noul answers for dimension {dimension!r} in run {run_id!r}")
    by_idx = {idx: (p, text) for idx, p, text in cands}
    path = labels_path(data_dir, run_id)
    doc = load_labels(path)
    if doc.get("dimension") not in (None, dimension):
        path = data_dir / f"{run_id}.labels.{dimension}.json"
        doc = load_labels(path)
    doc.setdefault("run_id", run_id)
    doc.setdefault("dimension", dimension)
    doc.setdefault("instructions", (spec or {}).get("instructions", ""))
    doc.setdefault("seed", 42 if seed is None else seed)
    doc.setdefault("n", n)
    doc.setdefault("created_at", datetime.now(UTC).isoformat(timespec="seconds"))
    counts = [0] * 5
    for _, p, _ in cands:
        counts[bucket_of(p)] += 1
    print(
        "candidates per bucket (0-.2, .2-.4, .4-.6, .6-.8, .8-1): " + " / ".join(map(str, counts)),
        file=sys.stderr,
    )
    if "sample" not in doc:
        doc["sample"] = stratified_sample(cands, n, doc["seed"])
        doc["batches"] = [{"n": n, "seed": doc["seed"], "at": doc["created_at"]}]
        save_labels(path, doc)
    elif seed is not None and not add:
        print(
            f"note: sample already stored in {path.name}; --seed only affects a new batch (--add)",
            file=sys.stderr,
        )
    if add > 0:
        batches = doc.setdefault(
            "batches", [{"n": doc["n"], "seed": doc["seed"], "at": doc["created_at"]}]
        )
        batch_seed = seed if seed is not None else doc["seed"] + len(batches)
        already = set(doc["sample"]) | {int(k) for k in doc["labels"]}
        fresh = stratified_sample(cands, add, batch_seed, exclude=already)
        doc["sample"] = doc["sample"] + fresh
        batches.append(
            {
                "n": len(fresh),
                "seed": batch_seed,
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
        )
        save_labels(path, doc)
        print(
            f"added {len(fresh)} fresh sentences (seed {batch_seed}); sample is now {len(doc['sample'])}",
            file=sys.stderr,
        )
    remaining = [i for i in doc["sample"] if str(i) not in doc["labels"]]
    if not remaining and not report_only:
        print("every sampled sentence is labelled; use --add N to draw fresh ones", file=sys.stderr)
    if not report_only:
        label_interactively(doc, doc["sample"], by_idx, doc["instructions"], path)
    table = calibration_table(doc)
    print(f"\nrun {run_id} · dimension {dimension} · labels {path}", file=sys.stderr)
    return table
