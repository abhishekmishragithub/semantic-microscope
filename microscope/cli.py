"""typer entry point. Thin: parse args, hand off to pipeline/stats/viewer."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from microscope.pipeline import MODEL, build_request, build_state
from microscope.presets import load_preset
from microscope.segment import segment

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Semantic Microscope: Jev-labelled document heatmaps.",
)
ROOT = Path(__file__).resolve().parent.parent


def api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("TYPESAFE_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        typer.echo("TYPESAFE_API_KEY not set (env or .env)", err=True)
        raise typer.Exit(2)
    return key


@app.command()
def spike() -> None:
    """Milestone 0: one raw request, then 20 timed calls."""
    import runpy

    os.environ.setdefault("TYPESAFE_API_KEY", api_key())
    runpy.run_path(str(ROOT / "spike.py"), run_name="__main__")


@app.command()
def label(
    file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    preset: str = typer.Option(
        "contract", help="Preset name in presets/ or a path to a YAML file."
    ),
    limit: int = typer.Option(500, help="Max sentences to label."),
    rps: float = typer.Option(15.0, help="Requests per second (token bucket)."),
    concurrency: int = typer.Option(12, help="Max in-flight requests."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the SQLite cache."),
    resume: bool = typer.Option(
        False, "--resume", help="Skip idx values already in the run's JSONL."
    ),
    run_id: str | None = typer.Option(None, help="Run id; defaults to <file>-<timestamp>."),
    chaos: bool = typer.Option(False, "--chaos", help="Inject a forced 429 and a forced timeout."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing run id."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print sentences and payloads, call nothing."
    ),
) -> None:
    """Label every sentence of FILE with the preset's questions."""
    p = load_preset(preset)
    sentences = segment(file.read_text(encoding="utf-8"))
    if dry_run:
        for s in sentences[:limit]:
            typer.echo(
                f"\n[{s.idx}] chars {s.char_start}-{s.char_end}"
                + ("  (too_short, skipped)" if s.too_short else "")
            )
            typer.echo(
                json.dumps(
                    build_request(build_state(sentences, s.idx, p.document_kind), p), indent=2
                )
            )
        typer.echo(
            f"\n{len(sentences)} sentences total, {min(limit, len(sentences))} shown, model {MODEL}",
            err=True,
        )
        return
    from microscope.pipeline import RunExists, run

    try:
        run(
            file,
            p,
            api_key(),
            limit=limit,
            rps=rps,
            concurrency=concurrency,
            use_cache=not no_cache,
            resume=resume,
            run_id=run_id,
            chaos=chaos,
            force=force,
        )
    except RunExists as e:
        typer.echo(f"{e}. Use --resume to continue it or --force to overwrite it.", err=True)
        raise typer.Exit(1) from None


@app.command()
def prepare(
    source: Annotated[str, typer.Argument(help="URL or path to an .html or .txt file.")],
    out: Annotated[
        Path | None, typer.Option(help="Output .txt; defaults to samples/<slug>.txt")
    ] = None,
    start_at: Annotated[
        str | None,
        typer.Option(help="Drop everything before the first paragraph starting with this."),
    ] = None,
    end_at: Annotated[
        str | None, typer.Option(help="Drop this paragraph and everything after it.")
    ] = None,
) -> None:
    """Turn a web page or raw text into clean paragraphs for `label`."""
    from microscope.prepare import prepare as _prepare
    from microscope.prepare import slug

    text, report = _prepare(source, start_at=start_at, end_at=end_at)
    out = out or ROOT / "samples" / f"{slug(source)}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    typer.echo(f"wrote {out}", err=True)
    typer.echo(report.summary(), err=True)


@app.command()
def serve(
    port: int = typer.Option(8000),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open a browser tab."),
    layout: str = typer.Option("lanes", help="Viewer layout to open: lanes or wall."),
) -> None:
    """Serve the repo root so viewer/ can fetch data/."""
    from microscope.viewer_server import serve as _serve

    if layout not in ("lanes", "wall"):
        typer.echo("--layout must be lanes or wall", err=True)
        raise typer.Exit(2)
    _serve(ROOT, port, open_browser=not no_open, layout=layout)


@app.command()
def stats(run_id: str) -> None:
    """Print the latency/cost report for a run."""
    from microscope.stats import report

    typer.echo(report(ROOT / "data", run_id))


@app.command()
def validate(
    run_id: str,
    dimension: str = typer.Option(..., help="A noul dimension name."),
    n: int = typer.Option(
        30, help="First-run sample size, spread across five probability buckets."
    ),
    seed: int | None = typer.Option(
        None, help="Sampling seed (default 42 on the first run). Only affects a new batch later."
    ),
    add: int = typer.Option(
        0, "--add", help="Draw N fresh sentences not yet sampled and append them to the sample."
    ),
    report_only: bool = typer.Option(
        False, "--report-only", help="Print the table from saved labels."
    ),
) -> None:
    """Calibration check: label sampled sentences blind, then compare to Jev's probabilities."""
    from microscope.validate import run_validate

    try:
        typer.echo(
            run_validate(
                ROOT / "data", run_id, dimension, n=n, seed=seed, report_only=report_only, add=add
            )
        )
    except (ValueError, FileNotFoundError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None


@app.command("calibration-chart")
def calibration_chart(
    run_id: str,
    dimension: str | None = typer.Option(
        None, help="Defaults to the dimension in the labels file."
    ),
    out: Annotated[
        Path | None, typer.Option(help="PNG path; defaults to docs/img/calibration-<run>-<dim>.png")
    ] = None,
    title: str | None = typer.Option(
        None, help="Chart title; defaults to '<dimension> · <run-id>'."
    ),
) -> None:
    """Render the validate table as predicted-vs-observed PNG (and SVG) for the blog."""
    import json

    from microscope.chart import rasterise, render_svg

    data = ROOT / "data"
    path = data / f"{run_id}.labels.json"
    if dimension and (alt := data / f"{run_id}.labels.{dimension}.json").exists():
        path = alt
    if not path.exists():
        typer.echo(f"no labels for run {run_id!r}; run `microscope validate` first", err=True)
        raise typer.Exit(1)
    doc = json.loads(path.read_text())
    if dimension and doc.get("dimension") != dimension:
        typer.echo(
            f"{path.name} holds dimension {doc.get('dimension')!r}, not {dimension!r}", err=True
        )
        raise typer.Exit(1)
    out = out or ROOT / "docs" / "img" / f"calibration-{run_id}-{doc.get('dimension', 'noul')}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    svg_path = out.with_suffix(".svg")
    svg_path.write_text(render_svg(doc, title))
    tool = rasterise(svg_path, out)
    typer.echo(f"wrote {svg_path}", err=True)
    if tool:
        typer.echo(f"wrote {out} (via {tool})", err=True)
    else:
        typer.echo(
            "no SVG rasteriser found (rsvg-convert, ImageMagick or macOS qlmanage); PNG not written",
            err=True,
        )
        raise typer.Exit(1)


if __name__ == "__main__":
    sys.exit(app())
