"""Latency and cost report for a run, computed from the JSONL (meta is a cross-check only)."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from microscope.jev import USD_PER_INPUT_TOKEN


def load_run(data_dir: Path, run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    jsonl, meta_path = data_dir / f"{run_id}.jsonl", data_dir / f"{run_id}.meta.json"
    if not jsonl.exists():
        raise FileNotFoundError(f"no run {run_id!r} in {data_dir}")
    recs = [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]
    recs.sort(key=lambda r: r["idx"])
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    return recs, meta


def pct(values: list[int | float]) -> dict[str, float | None]:
    if not values:
        return dict.fromkeys(("p50", "p95", "p99", "max"))
    if len(values) < 2:
        v = values[0]
        return {"p50": v, "p95": v, "p99": v, "max": v}
    q = statistics.quantiles(values, n=100, method="inclusive")
    return {"p50": q[49], "p95": q[94], "p99": q[98], "max": max(values)}


def summarize(recs: list[dict[str, Any]]) -> dict[str, Any]:
    live = [r for r in recs if not r["cached"] and not r["error"]]
    lat = [r["latency_ms"] for r in live]
    srv = [r["server_ms"] for r in live if r.get("server_ms") is not None]
    net = [r["latency_ms"] - r["server_ms"] for r in live if r.get("server_ms") is not None]
    tokens = sum(r["input_tokens"] for r in recs)
    errors = [r for r in recs if r["error"] and r["error"] != "too_short"]
    return {
        "n_records": len(recs),
        "n_live": len(live),
        "n_cached": sum(1 for r in recs if r["cached"]),
        "n_too_short": sum(1 for r in recs if r["error"] == "too_short"),
        "n_errors": len(errors),
        "error_kinds": sorted({r["error"].split(":")[0] for r in errors}),
        "n_429": sum(r.get("n_429", 0) for r in recs),
        "n_retried": sum(1 for r in recs if r.get("attempts", 0) > 1),
        "latency_ms": pct(lat),
        "server_ms": pct(srv),
        "network_ms": pct(net),
        "tokens_per_sentence": pct([r["input_tokens"] for r in live]),
        "total_input_tokens": tokens,
        "estimated_cost_usd": tokens * USD_PER_INPUT_TOKEN,
        "models_seen": sorted({r["model"] for r in recs if r.get("model")}),
    }


def _row(label: str, p: dict[str, Any], unit: str = "") -> str:
    def f(v: Any) -> str:
        return "-" if v is None else f"{v:,.0f}{unit}"

    return f"  {label:<22} p50 {f(p['p50']):>9}   p95 {f(p['p95']):>9}   p99 {f(p['p99']):>9}   max {f(p['max']):>9}"


def report(data_dir: Path, run_id: str) -> str:
    recs, meta = load_run(data_dir, run_id)
    s = summarize(recs)
    lines = [
        f"run {run_id}",
        (
            f"  source {meta.get('source_file', '?')}   preset {meta.get('preset', '?')}   "
            f"model {', '.join(s['models_seen']) or '?'}"
        ),
        (
            f"  wall {meta.get('wall_seconds', '?')}s   rps {meta.get('rps', '?')}   "
            f"concurrency {meta.get('concurrency', '?')}   started {meta.get('started_at', '?')}"
        ),
        "",
        f"  sentences {s['n_records']}   live {s['n_live']}   cached {s['n_cached']}   "
        f"too_short {s['n_too_short']}   errors {s['n_errors']}"
        + (f" ({', '.join(s['error_kinds'])})" if s["error_kinds"] else ""),
        f"  429s {s['n_429']}   requests retried {s['n_retried']}",
        "",
        _row("round trip (client)", s["latency_ms"], "ms"),
        _row("server (envoy header)", s["server_ms"], "ms"),
        _row("network + TLS", s["network_ms"], "ms"),
        _row("input tokens / sent.", s["tokens_per_sentence"]),
        "",
        (
            f"  total input tokens {s['total_input_tokens']:,}   "
            f"estimated cost ${s['estimated_cost_usd']:.4f}   "
            f"(${USD_PER_INPUT_TOKEN * 1e6:.3f} per Mtok, output free)"
        ),
    ]
    if meta.get("drifted_models"):
        lines.append(f"  WARNING model drift: {meta['drifted_models']}")
    if s["n_live"] and s["latency_ms"]["p50"] is not None:
        per_s = s["n_live"] / meta["wall_seconds"] if meta.get("wall_seconds") else None
        if per_s:
            lines.append(f"  throughput {per_s:.1f} sentences/s over the run")
    return "\n".join(lines)
