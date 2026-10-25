"""Local dashboard for one shipped report.

The server binds to 127.0.0.1 only. Pages escape text. A quote link opens the
raw file and highlights the same byte span the quote check accepted. Paths
that leave the corpus are refused.
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from src.consolidate.quotes import highlight_latin1, resolve_corpus_file


def make_server(
    report_path: Path, corpus: Path, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    """HTTP server for the report and raw messages. Host must stay local."""
    if host != "127.0.0.1":
        raise ValueError("dashboard binds to 127.0.0.1 only")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    handler = _handler_class(report, corpus.resolve())
    return ThreadingHTTPServer((host, port), handler)


def serve(report_path: Path, corpus: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Serve until the process is stopped."""
    from src.gates import GateError

    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("complete"):
        raise GateError("serve", ["report is not a complete run"])
    server = make_server(report_path, corpus, host, port)
    print(f"dashboard: http://{host}:{server.server_address[1]}/")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def render_dashboard(report: dict) -> str:
    """The ranked opportunity page."""
    rows = []
    for item in report["opportunities"]:
        counts = item.get("participant_counts") or {}
        identifier = html.escape(quote(item["id"]))
        rows.append(
            "<tr>"
            f"<td><a href=\"/opportunity?id={identifier}&focus=formula\">"
            f"{html.escape(item['title'])}</a></td>"
            f"<td><a href=\"/opportunity?id={identifier}&focus=days\">"
            f"{item['occurrences_per_month']:.2f}</a></td>"
            f"<td><a href=\"/opportunity?id={identifier}&focus=formula\">"
            f"{item['hours_per_month']:.2f}</a></td>"
            f"<td><a href=\"/opportunity?id={identifier}&focus=formula\">"
            f"${item['dollars_per_month']:.2f}</a></td>"
            f"<td>{html.escape(', '.join(item.get('mailboxes') or []))}</td>"
            f"<td><a href=\"/opportunity?id={identifier}&focus=participants\">"
            f"{counts.get('senders', 0)} senders / {counts.get('recipients', 0)} recipients</a></td>"
            "</tr>"
        )
    sensitivity = report["sensitivity"]
    coverage = report["coverage"]
    dispositions = coverage.get("dispositions") or {}
    unreviewed = dispositions.get("ingested_unreviewed", 0)
    final = report.get("final_dispositions") or {}
    body = f"""
    <p class="scope">{html.escape(report["scope"])}</p>
    <p class="total"><a href="/calculation?metric=total_dollars">${report["total_dollars_per_month"]:.2f}</a>
    <span>per month · <a href="/calculation?metric=total_hours">{report["total_hours_per_month"]:.2f} hours</a></span></p>
    <p class="note">Sensitivity at the neighboring minute rungs:
    <a href="/calculation?metric=dollars_low">${sensitivity["dollars_low"]:.2f}</a> to
    <a href="/calculation?metric=dollars_high">${sensitivity["dollars_high"]:.2f}</a>.
    Assumptions are labeled on each opportunity and are not evidence.</p>
    <table>
      <thead><tr><th>Process</th><th>Days / month</th><th>Hours</th><th>Dollars</th>
      <th>Mailboxes</th><th>People</th></tr></thead>
      <tbody>{"".join(rows) or "<tr><td colspan='6'>No published opportunities.</td></tr>"}</tbody>
    </table>
    <h2>Excluded from the total</h2>
    <p>Incidents: {len(report["incidents"])}. Secondary interpretations: {len(report["secondary"])}.
    Ingested but not semantically reviewed: {unreviewed}.
    Accepted: {final.get("accepted", 0)}. Rejected: {final.get("rejected", 0)}.
    Duplicates: {final.get("duplicate", 0)}. Parse failures: {final.get("parse_failure", 0)}.</p>
    <ul>
    {"".join(
        f"<li><a href=\"/opportunity?id={html.escape(quote(item['id']))}\">Incident: {html.escape(item['title'])}</a> — {item['occurrences']} active days</li>"
        for item in report["incidents"]
    )}
    {"".join(
        f"<li>Secondary {html.escape(item['process_id'])}: {len(item['paths'])} paths</li>"
        for item in report["secondary"]
    )}
    </ul>
    """
    return _page("Opportunities", body)


def render_opportunity(report: dict, opportunity_id: str) -> str | None:
    """One opportunity, its formula, and links into the raw files."""
    item = next((row for row in report["opportunities"] if row["id"] == opportunity_id), None)
    pool = "published"
    if item is None:
        item = next((row for row in report["incidents"] if row["id"] == opportunity_id), None)
        pool = "incident"
    if item is None:
        return None
    formula = item["formula"]
    days = "".join(
        f"<li>{html.escape(day['date'])}: "
        + ", ".join(
            f"<a href=\"/message?path={html.escape(quote(path))}\">{html.escape(path)}</a>"
            for path in day["paths"]
        )
        + "</li>"
        for day in item["occurrence_days"]
    )
    quotes = []
    for claim in item["quotes"]:
        href = _message_link(claim["path"], claim["quote"], claim.get("byte_start"), claim.get("byte_end"))
        kind = claim["kind"]
        if claim.get("stall_type"):
            kind = f"{kind} / {claim['stall_type']}"
        quotes.append(
            f"<li><a href=\"{href}\">{html.escape(claim['quote'])}</a> "
            f"<span class=\"note\">{html.escape(kind)} · {html.escape(claim['path'])} "
            f"· {html.escape(claim['evidence_status'])}</span></li>"
        )
    delay = item.get("delay")
    delay_html = ""
    if delay:
        citations = ", ".join(
            f"<a href=\"{_message_link(cite['path'], '', None, None)}\">{html.escape(cite['path'])}</a> "
            f"({html.escape(cite.get('date_raw') or '')})"
            for cite in delay.get("citations") or []
        )
        chases = ", ".join(
            f"<a href=\"{_message_link(path, '', None, None)}\">{html.escape(path)}</a>"
            for path in delay.get("chase_paths") or []
        )
        delay_html = (
            f"<p id=\"delay\">Median reply gap {delay['median_reply_hours']:.2f} hours. "
            f"Citations: {citations}. Chase count: {delay.get('chase_count', 0)}. "
            f"Chase messages: {chases or 'none'}.</p>"
        )
    cadence = item.get("stated_cadence")
    cadence_href = ((item.get("provenance") or {}).get("cadence") or {}).get("href")
    if cadence and cadence_href:
        cadence_html = f"<p id=\"cadence\">Stated cadence: <a href=\"{html.escape(cadence_href)}\">{html.escape(cadence)}</a></p>"
    elif cadence:
        cadence_html = f"<p id=\"cadence\">Stated cadence: {html.escape(cadence)}</p>"
    else:
        cadence_html = ""
    assumptions = item["assumptions"]
    excluded = " This incident is excluded from the total." if pool == "incident" else ""
    people = _participant_html(item.get("participant_paths") or {})
    artifact = item.get("artifact") or {}
    artifact_html = ""
    if artifact:
        artifact_html = (
            "<h2 id=\"artifact\">Draft</h2>"
            f"<p>Kind: {html.escape(str(artifact.get('kind') or ''))}. Validation: passed.</p>"
            f"<pre>{html.escape(artifact.get('proposed_language') or '')}</pre>"
        )
    body = f"""
    <p><a href="/">Total</a> → {html.escape(item["title"])}</p>
    <h1>{html.escape(item["title"])}</h1>
    <p><a href="#formula">${item["dollars_per_month"]:.2f}</a> per month
    (<a href="#formula">{item["hours_per_month"]:.2f} hours</a>,
    <a href="#days">{item["occurrences"]} active days</a>).{html.escape(excluded)}</p>
    {delay_html}
    {cadence_html}
    <h2 id="formula">Formula</h2>
    <p>{html.escape(formula["expression"])}</p>
    <ul>
      <li>Active days: {formula["occurrences"]}</li>
      <li>Span days: {formula["span_days"]}</li>
      <li>Months: {formula["months"]}</li>
      <li>Minutes (assumption): {assumptions["minutes"]}</li>
      <li>Fraction (assumption): {assumptions["fraction"]}</li>
      <li>Rate: ${formula["rate"]}</li>
    </ul>
    <p class="note">{html.escape(assumptions.get("rationale") or "")}</p>
    <h2 id="participants">People</h2>
    {people}
    <h2 id="days">Occurrence days</h2>
    <ul>{days or "<li>None dated.</li>"}</ul>
    <h2 id="evidence">Evidence</h2>
    <ul>{"".join(quotes) or "<li>None.</li>"}</ul>
    {artifact_html}
    """
    return _page(item["title"], body)


def render_message(
    corpus: Path,
    relative_path: str,
    quote_text: str,
    start: int | None = None,
    end: int | None = None,
) -> tuple[int, str]:
    """Raw message page, or a 400 page when the path is outside the corpus."""
    resolved = resolve_corpus_file(corpus, relative_path)
    if resolved is None:
        page = _page("Unavailable", "<p>That path is not a file in this corpus.</p>")
        return 400, page
    raw = resolved.read_bytes()
    text = raw.decode("latin-1")
    display = f"<p class=\"note\">Display text: {html.escape(quote_text)}</p>" if quote_text else ""
    if start is not None and end is not None and 0 <= start < end <= len(text):
        body_html = (
            f"{display}<pre>{html.escape(text[:start])}<mark>{html.escape(text[start:end])}</mark>"
            f"{html.escape(text[end:])}</pre>"
        )
    else:
        parts = highlight_latin1(raw, quote_text) if quote_text else None
        if parts is None:
            body_html = f"{display}<pre>{html.escape(text)}</pre>"
        else:
            before, middle, after = parts
            body_html = (
                f"{display}<pre>{html.escape(before)}<mark>{html.escape(middle)}</mark>"
                f"{html.escape(after)}</pre>"
            )
    page = _page(
        relative_path,
        f"<p><a href=\"/\">Total</a> → raw file</p><h1>{html.escape(relative_path)}</h1>{body_html}",
    )
    return 200, page


def render_calculation(report: dict, metric: str) -> str | None:
    """The opportunities and messages that add up to one total."""
    fields = {
        "total_dollars": "dollars_per_month",
        "total_hours": "hours_per_month",
        "dollars_low": "dollars_low",
        "dollars_high": "dollars_high",
        "hours_low": "hours_low",
        "hours_high": "hours_high",
    }
    field = fields.get(metric)
    if field is None:
        return None
    rows = []
    total = 0.0
    for item in report["opportunities"]:
        value = float(item[field])
        total += value
        links = []
        for day in item.get("occurrence_days") or []:
            for path in day.get("paths") or []:
                links.append(f"<a href=\"{_message_link(path, '', None, None)}\">{html.escape(path)}</a>")
        shown = ", ".join(links) if links else "no dated messages"
        rows.append(
            "<tr>"
            f"<td><a href=\"/opportunity?id={html.escape(quote(item['id']))}&focus=formula\">"
            f"{html.escape(item['title'])}</a></td>"
            f"<td>{value:.2f}</td>"
            f"<td>{html.escape(item['formula']['expression'])}</td>"
            f"<td>{shown}</td>"
            "</tr>"
        )
    body = f"""
    <p><a href="/">Total</a> → calculation</p>
    <h1>{html.escape(metric)}</h1>
    <p>Sum of published opportunities: {total:.2f}.</p>
    <p class="note">occurrences / months * minutes / 60 * fraction * 85</p>
    <table>
      <thead><tr><th>Process</th><th>Value</th><th>Expression</th><th>Messages</th></tr></thead>
      <tbody>{"".join(rows) or "<tr><td colspan='4'>No published opportunities.</td></tr>"}</tbody>
    </table>
    """
    return _page(metric, body)


def _page(title: str, body: str) -> str:
    """One HTML page with the shared stylesheet."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body {{ margin: 0; font-family: "Iowan Old Style", Palatino, Georgia, serif;
         background: #f3efe6; color: #1d1a16; }}
  main {{ max-width: 960px; margin: 0 auto; padding: 40px 24px 80px; }}
  h1, h2 {{ font-weight: 600; }}
  .total {{ font-size: 56px; margin: 8px 0; letter-spacing: -0.03em; }}
  .total span, .note, .scope {{ color: #5c564c; font-size: 16px; }}
  table {{ width: 100%; border-collapse: collapse; background: white; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e4dccb; }}
  th {{ font-size: 13px; letter-spacing: 0.04em; text-transform: uppercase; color: #5c564c; }}
  a {{ color: #1f4d3a; }}
  mark {{ background: #f3d27a; }}
  pre {{ white-space: pre-wrap; background: white; padding: 16px; }}
</style>
</head>
<body><main>{body}</main></body>
</html>
"""


def _handler_class(report: dict, corpus: Path) -> type[BaseHTTPRequestHandler]:
    """Request handler bound to one report and one corpus."""
    class Handler(BaseHTTPRequestHandler):
        """Serves the report pages for one corpus."""

        def do_GET(self) -> None:  # noqa: N802
            """Serve the dashboard, one opportunity, or one raw message."""
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/":
                self._send(200, render_dashboard(report))
                return
            if parsed.path == "/opportunity":
                page = render_opportunity(report, query.get("id", [""])[0])
                self._send(200 if page else 404, page or _page("Missing", "<p>Unknown opportunity.</p>"))
                return
            if parsed.path == "/calculation":
                page = render_calculation(report, query.get("metric", [""])[0])
                self._send(200 if page else 404, page or _page("Missing", "<p>Unknown total.</p>"))
                return
            if parsed.path == "/message":
                start = _optional_int(query.get("start", [None])[0])
                end = _optional_int(query.get("end", [None])[0])
                status, page = render_message(
                    corpus,
                    query.get("path", [""])[0],
                    query.get("quote", [""])[0],
                    start,
                    end,
                )
                self._send(status, page)
                return
            self._send(404, _page("Missing", "<p>Not found.</p>"))

        def _send(self, status: int, page: str) -> None:
            """Write one HTML response."""
            encoded = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, fmt: str, *args) -> None:
            """Discard the default access log."""
            return

    return Handler


def _message_link(path: str, quote_text: str, start: int | None, end: int | None) -> str:
    """Escaped href for one raw message and optional highlight."""
    href = "/message?path=" + html.escape(quote(path, safe="")) + "&quote=" + html.escape(quote(quote_text, safe=""))
    if start is not None and end is not None:
        href += f"&start={int(start)}&end={int(end)}"
    return href


def _participant_html(paths_by_role: dict) -> str:
    """Address counts with a link to every message that produced them."""
    if not paths_by_role:
        return "<p>None recorded.</p>"
    items = []
    for role, addresses in paths_by_role.items():
        for addr, paths in addresses.items():
            links = ", ".join(
                f"<a href=\"{_message_link(path, '', None, None)}\">{html.escape(path)}</a>"
                for path in paths
            )
            items.append(f"<li>{html.escape(role)} {html.escape(addr)} ({len(paths)}): {links}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _optional_int(value: str | None) -> int | None:
    """An integer query parameter, or null when it is absent or not an integer."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None
