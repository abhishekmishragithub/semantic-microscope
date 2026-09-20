"""Orchestration: sentence -> state -> one Jev request carrying every question -> JSONL line."""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from microscope.cache import Cache, cache_key
from microscope.jev import MODEL, USD_PER_INPUT_TOKEN, AuthError, Chaos, JevClient, JevResult
from microscope.presets import Preset
from microscope.segment import Sentence, segment

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class RunExists(FileExistsError):
    pass


def build_state(sentences: list[Sentence], i: int, document_kind: str) -> dict[str, Any]:
    prev = sentences[i - 1].text if i > 0 else None
    nxt = sentences[i + 1].text if i + 1 < len(sentences) else None
    return {
        "document_kind": document_kind,
        "previous_sentence": prev,
        "sentence": sentences[i].text,
        "next_sentence": nxt,
    }


def build_request(state: dict[str, Any], preset: Preset, model: str = MODEL) -> dict[str, Any]:
    return {"state": state, "model": model, "questions": preset.questions()}


def _record(s: Sentence, res: JevResult, *, cached: bool) -> dict[str, Any]:
    return {
        "idx": s.idx,
        "char_start": s.char_start,
        "char_end": s.char_end,
        "text": s.text,
        "answers": res.answers,
        "latency_ms": res.latency_ms,
        "server_ms": res.server_ms,
        "input_tokens": res.input_tokens,
        "cached": cached,
        "error": res.error,
        "model": res.model,
        "request_id": res.request_id,
        "attempts": res.attempts,
        "n_429": res.n_429,
    }


def _percentiles(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"p50": None, "p95": None, "p99": None, "max": None}
    if len(values) < 2:
        return {"p50": values[0], "p95": values[0], "p99": values[0], "max": values[0]}
    q = statistics.quantiles(values, n=100, method="inclusive")
    return {"p50": round(q[49]), "p95": round(q[94]), "p99": round(q[98]), "max": max(values)}


def completed_indices(jsonl: Path) -> set[int]:
    if not jsonl.exists():
        return set()
    done = set()
    for line in jsonl.read_text().splitlines():
        if line.strip():
            done.add(json.loads(line)["idx"])
    return done


def latest_run_id(data_dir: Path, stem: str) -> str | None:
    runs = sorted(p.stem for p in data_dir.glob(f"{stem}-*.jsonl"))
    return runs[-1] if runs else None


class Progress:
    def __init__(self, total: int) -> None:
        self.total, self.done, self.errors, self.tokens, self.cached = total, 0, 0, 0, 0
        self.latencies: list[int] = []
        self.n_429 = 0

    def add(self, rec: dict[str, Any]) -> None:
        self.done += 1
        self.tokens += rec["input_tokens"]
        self.n_429 += rec["n_429"]
        if rec["error"] and rec["error"] != "too_short":
            self.errors += 1
        if rec["cached"]:
            self.cached += 1
        elif not rec["error"]:
            self.latencies.append(rec["latency_ms"])
        p50 = _percentiles(self.latencies)["p50"] or 0
        cost = self.tokens * USD_PER_INPUT_TOKEN
        print(
            f"\r{self.done}/{self.total} · p50 {p50}ms · ${cost:.4f} · {self.errors} errors · "
            f"{self.cached} cached",
            end="",
            file=sys.stderr,
            flush=True,
        )


async def _run_async(
    sentences: list[Sentence],
    todo: list[Sentence],
    preset: Preset,
    api_key: str,
    out: Path,
    cache: Cache | None,
    prog: Progress,
    *,
    rps: float,
    concurrency: int,
    chaos: bool,
) -> dict[str, Any]:
    digest = preset.digest()
    drift: set[str] = set()
    async with JevClient(
        api_key, rps=rps, concurrency=concurrency, chaos=Chaos() if chaos else None
    ) as jev:
        await jev.warm()
        with out.open("a", encoding="utf-8") as fh:

            def emit(rec: dict[str, Any]) -> None:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                prog.add(rec)

            async def one(s: Sentence) -> None:
                if s.too_short:
                    emit(_record(s, JevResult(error="too_short"), cached=False))
                    return
                state = build_state(sentences, s.idx, preset.document_kind)
                key = cache_key(
                    s.text, state["previous_sentence"], state["next_sentence"], digest, jev.model
                )
                if cache and (hit := cache.get(key)):
                    emit(_record(s, JevResult(**hit), cached=True))
                    return
                res = await jev.ask(state, preset.questions())
                if not res.error and cache:
                    cache.put(
                        key,
                        {
                            "answers": res.answers,
                            "input_tokens": res.input_tokens,
                            "model": res.model,
                            "server_ms": res.server_ms,
                        },
                    )
                emit(_record(s, res, cached=False))

            await asyncio.gather(*(one(s) for s in todo))
        drift = jev.drifted_models
    return {"drifted_models": sorted(drift)}


def run(
    file: Path,
    preset: Preset,
    api_key: str,
    *,
    limit: int = 500,
    rps: float = 15.0,
    concurrency: int = 12,
    use_cache: bool = True,
    resume: bool = False,
    run_id: str | None = None,
    chaos: bool = False,
    force: bool = False,
    data_dir: Path = DATA_DIR,
) -> str:
    data_dir.mkdir(exist_ok=True)
    text = file.read_text(encoding="utf-8")
    sentences = segment(text)
    if resume and not run_id:
        run_id = latest_run_id(data_dir, file.stem)
    run_id = run_id or f"{file.stem}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    out, meta_path = data_dir / f"{run_id}.jsonl", data_dir / f"{run_id}.meta.json"
    done = completed_indices(out) if resume else set()
    if not resume and out.exists():
        if not force:
            raise RunExists(f"run {run_id!r} already exists at {out}")
        out.unlink()
    todo = [s for s in sentences[:limit] if s.idx not in done]
    prog = Progress(len(todo))
    cache = Cache(data_dir / "cache.sqlite") if use_cache else None
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    extra: dict[str, Any] = {}
    try:
        extra = asyncio.run(
            _run_async(
                sentences,
                todo,
                preset,
                api_key,
                out,
                cache,
                prog,
                rps=rps,
                concurrency=concurrency,
                chaos=chaos,
            )
        )
    except AuthError as e:
        print(f"\nfatal: {e}", file=sys.stderr)
    finally:
        if cache:
            cache.close()
    wall = time.perf_counter() - t0
    print(file=sys.stderr)
    meta = {
        "run_id": run_id,
        "source_file": str(file),
        "preset": preset.name,
        "model_requested": MODEL,
        "jev_model_reported": (extra.get("drifted_models") or [MODEL])[0] if extra else None,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "wall_seconds": round(wall, 2),
        "n_sentences": min(limit, len(sentences)),
        "n_sentences_in_document": len(sentences),
        "n_labelled_this_run": prog.done,
        "n_resumed": len(done),
        "n_errors": prog.errors,
        "n_cached": prog.cached,
        "n_too_short": sum(1 for s in sentences[:limit] if s.too_short),
        "latency_ms": _percentiles(prog.latencies),
        "total_input_tokens": prog.tokens,
        "estimated_cost_usd": round(prog.tokens * USD_PER_INPUT_TOKEN, 6),
        "rate_limit_429s": prog.n_429,
        "rps": rps,
        "concurrency": concurrency,
        "chaos": chaos,
        "drifted_models": extra.get("drifted_models", []),
        "questions": preset.as_dict(),
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"wrote {out} and {meta_path}", file=sys.stderr)
    return run_id
