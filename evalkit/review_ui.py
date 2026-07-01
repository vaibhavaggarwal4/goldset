from __future__ import annotations

import html
import json
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from evalkit.errors import UserFacingError
from evalkit.storage import EvalStore


def serve_review_ui(store: EvalStore, run_id: str, host: str, port: int) -> None:
    handler = _make_handler(store, run_id)
    try:
        server = LocalReviewServer((host, port), handler)
    except OSError as exc:
        raise UserFacingError(
            f"Could not start the review UI on {host}:{port}.\n"
            "Fix: choose another port with --port, for example --port 8766."
        ) from exc
    print(f"Review UI: http://{host}:{port}/?run_id={run_id}", flush=True)
    server.serve_forever()


class LocalReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def _make_handler(store: EvalStore, run_id: str):
    class ReviewHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._send_html(_render_review_page(store, run_id))

        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            payload = parse_qs(self.rfile.read(length).decode("utf-8"))
            score = payload.get("score", [""])[0]
            store.save_human_review(
                run_id=run_id,
                case_id=payload["case_id"][0],
                dimension_name=payload["dimension_name"][0],
                reviewer=payload.get("reviewer", ["reviewer"])[0] or "reviewer",
                passed=payload.get("passed", ["fail"])[0] == "pass",
                score=float(score) if score else None,
                notes=payload.get("notes", [""])[0],
                correction=payload.get("correction", [""])[0],
                failure_reason=payload.get("failure_reason", [""])[0],
                rubric_issue=payload.get("rubric_issue", ["off"])[0] == "on",
            )
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def _send_html(self, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args) -> None:
            return

    return ReviewHandler


def _render_review_page(store: EvalStore, run_id: str) -> str:
    cases = {row["case_id"]: row for row in store.case_rows(run_id)}
    dimensions = [row for row in store.dimension_rows(run_id) if row["requires_human_review"] == 1 or row["passed"] is None]
    rows = "\n".join(_review_card(cases[row["case_id"]], row) for row in dimensions)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Human Review</title>
  <style>
    body {{ font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f8fafc; color: #111827; }}
    header {{ background: #111827; color: white; padding: 24px 32px; }}
    main {{ max-width: 980px; padding: 24px 32px 48px; }}
    article {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 18px; margin-bottom: 16px; }}
    label {{ display: block; font-weight: 650; margin: 12px 0 6px; }}
    textarea, input, select {{ width: 100%; box-sizing: border-box; padding: 9px 10px; border: 1px solid #cbd5e1; border-radius: 6px; font: inherit; }}
    textarea {{ min-height: 78px; }}
    input[type="checkbox"] {{ width: auto; }}
    button {{ margin-top: 12px; border: 0; border-radius: 6px; background: #2563eb; color: white; padding: 10px 14px; font-weight: 700; cursor: pointer; }}
    pre {{ white-space: pre-wrap; background: #f1f5f9; padding: 12px; border-radius: 6px; }}
    .muted {{ color: #64748b; }}
  </style>
</head>
<body>
  <header><h1>Human Review</h1><div>Run {html.escape(run_id)}</div></header>
  <main>{rows or '<p>No dimensions currently require human review.</p>'}</main>
</body>
</html>
"""


def _review_card(case_row, dimension_row) -> str:
    fields = json.loads(case_row["fields_json"] or "{}")
    return f"""<article>
  <h2>{html.escape(case_row['case_id'])} · {html.escape(dimension_row['dimension_name'])}</h2>
  <p class="muted">Machine result: {_status_text(dimension_row['passed'])} · Score: {dimension_row['score'] or 'N/A'}</p>
  <label>Input</label>
  <pre>{html.escape(case_row['input_text'] or '')}</pre>
  <label>Artifact</label>
  <pre>{html.escape(case_row['artifact_content'] or '')}</pre>
  <details><summary>Fields</summary><pre>{html.escape(json.dumps(fields, indent=2))}</pre></details>
  <label>Machine rationale</label>
  <pre>{html.escape(dimension_row['rationale'] or '')}</pre>
  <form method="post">
    <input type="hidden" name="case_id" value="{html.escape(case_row['case_id'])}">
    <input type="hidden" name="dimension_name" value="{html.escape(dimension_row['dimension_name'])}">
    <label>Reviewer</label>
    <input name="reviewer" value="reviewer">
    <label>Human judgment</label>
    <select name="passed"><option value="pass">Pass</option><option value="fail">Fail</option></select>
    <label>Score</label>
    <input name="score" type="number" min="1" max="5" step="0.5">
    <label>Failure reason</label>
    <input name="failure_reason" placeholder="generic, off_brand, unclear_cta, wrong_audience, judge_wrong">
    <label>Correction or shipped version</label>
    <textarea name="correction"></textarea>
    <label><input name="rubric_issue" type="checkbox"> Rubric needs refinement</label>
    <label>Notes</label>
    <textarea name="notes"></textarea>
    <button type="submit">Save Review</button>
  </form>
</article>"""


def _status_text(value: int | None) -> str:
    if value is None:
        return "Pending"
    return "Pass" if value == 1 else "Fail"
