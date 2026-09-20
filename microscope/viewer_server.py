"""Serve the repo root with stdlib http.server plus one JSON endpoint listing runs in data/."""

from __future__ import annotations

import json
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, root: Path, **kwargs: object) -> None:
        self.root = root
        super().__init__(*args, directory=str(root), **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:
        if self.path == "/":
            self.send_response(302)
            self.send_header("Location", "/viewer/")
            self.end_headers()
            return
        if self.path.split("?")[0] == "/api/runs":
            self._json(list_runs(self.root / "data"))
            return
        super().do_GET()

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        if "/data/" in str(args[0]) or "/api/" in str(args[0]):
            sys.stderr.write(f"  {fmt % args}\n")


def list_runs(data_dir: Path) -> list[dict[str, object]]:
    runs = []
    for meta_path in data_dir.glob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            continue
        runs.append(
            {
                "run_id": meta.get("run_id", meta_path.name.removesuffix(".meta.json")),
                "source_file": meta.get("source_file"),
                "preset": meta.get("preset"),
                "n_sentences": meta.get("n_sentences"),
                "n_errors": meta.get("n_errors"),
                "started_at": meta.get("started_at"),
                "finished_at": meta.get("finished_at"),
            }
        )
    runs.sort(key=lambda r: str(r.get("finished_at") or ""), reverse=True)
    return runs


def serve(root: Path, port: int = 8000, open_browser: bool = True, layout: str = "lanes") -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, root=root))
    url = f"http://127.0.0.1:{port}/viewer/" + (f"?layout={layout}" if layout != "lanes" else "")
    print(f"serving {root} at {url}  (ctrl-c to stop)", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
    finally:
        server.server_close()
