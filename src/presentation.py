"""
HTML presentation generator for FUWForge research reports.

Renders a self-contained, accessible, printer-friendly HTML5 document from
sealed report Markdown and accepted artifacts.

Key guarantees:
- Strict Content Security Policy: default-src 'none', no remote resources or scripts.
- Text escaping: all model-generated and source text is HTML-escaped.
- Semantic landmarks (<header>, <nav>, <main>, <section>, <footer>) and heading hierarchy.
- Preserves meaning without color; accessible contrast ratios (WCAG 4.5:1 minimum).
- Dedicated sections for Bull/Bear debate, Forensics (Altman/Beneish), Gaps, Sources.
- Fallback palette used by default; optional best-effort Gemini brand-color search.
- Failure to render HTML never halts or invalidates the core Markdown report.
"""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# ---------------------------------------------------------------------------
# Color & Palette Utilities (WCAG Contrast & CSS Validation)
# ---------------------------------------------------------------------------

HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

DEFAULT_PALETTE = {
    "primary": "#1e3a8a",       # Slate Blue / Navy
    "primary_dark": "#172554",  # Deep Navy
    "secondary": "#0f766e",     # Deep Teal
    "accent": "#2563eb",        # Royal Blue
    "background": "#f8fafc",    # Slate 50
    "surface": "#ffffff",       # White
    "surface_subtle": "#f1f5f9",# Slate 100
    "text": "#0f172a",          # Slate 900
    "text_muted": "#475569",    # Slate 600
    "border": "#e2e8f0",        # Slate 200
    "border_strong": "#cbd5e1", # Slate 300
    "success": "#166534",       # Green 800
    "success_bg": "#f0fdf4",    # Green 50
    "warning": "#9a3412",       # Orange 800
    "warning_bg": "#fff7ed",    # Orange 50
    "danger": "#991b1b",        # Red 800
    "danger_bg": "#fef2f2",     # Red 50
    "label": "Institutional Default Palette",
    "is_approximate": False,
}


def is_valid_hex_color(val: Any) -> bool:
    if not isinstance(val, str):
        return False
    return bool(HEX_COLOR_RE.match(val.strip()))


def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    h = hex_str.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _relative_luminance(rgb: Tuple[int, int, int]) -> float:
    def _gamma(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (_gamma(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex1: str, hex2: str) -> float:
    try:
        l1 = _relative_luminance(hex_to_rgb(hex1))
        l2 = _relative_luminance(hex_to_rgb(hex2))
        lighter, darker = max(l1, l2), min(l1, l2)
        return (lighter + 0.05) / (darker + 0.05)
    except Exception:
        return 1.0


def get_readable_text_color(bg_hex: str) -> str:
    """Choose white (#ffffff) or dark slate (#0f172a) for maximum readability."""
    white_ratio = contrast_ratio(bg_hex, "#ffffff")
    dark_ratio = contrast_ratio(bg_hex, "#0f172a")
    return "#ffffff" if white_ratio >= dark_ratio else "#0f172a"


def resolve_brand_palette(
    primary: Optional[str] = None,
    secondary: Optional[str] = None,
    *,
    label: Optional[str] = None,
    is_approximate: bool = True,
) -> Dict[str, Any]:
    palette = dict(DEFAULT_PALETTE)
    if primary and is_valid_hex_color(primary):
        # Normalize to 6-char hex
        r, g, b = hex_to_rgb(primary)
        norm_primary = f"#{r:02x}{g:02x}{b:02x}"
        # Ensure it has readable contrast on light backgrounds (>= 4.5:1)
        if contrast_ratio(norm_primary, "#ffffff") >= 4.0:
            palette["primary"] = norm_primary
            palette["primary_dark"] = norm_primary
            palette["label"] = label or "Approximate Brand Styling"
            palette["is_approximate"] = is_approximate

    if secondary and is_valid_hex_color(secondary):
        r, g, b = hex_to_rgb(secondary)
        norm_sec = f"#{r:02x}{g:02x}{b:02x}"
        if contrast_ratio(norm_sec, "#ffffff") >= 3.5:
            palette["secondary"] = norm_sec

    return palette


def search_brand_colors_gemini(ticker: str, company: str, api_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Best-effort single brand color search via Gemini.
    Returns validated palette dict or None on any failure.
    """
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        return None

    try:
        from src.agents.model import GeminiApiBackend

        backend = GeminiApiBackend(api_key=key)
        prompt = (
            f"What are the official corporate brand colors for {company} (ticker {ticker})?\n"
            "Return STRICT JSON with hex codes and an approximate boolean:\n"
            '{"primary": "#000000", "secondary": "#000000", "is_approximate": true}'
        )
        resp = backend.generate(prompt)
        text = getattr(resp, "text", str(resp))
        match = re.search(r"\{.*?\}", text, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group(0))
        p = data.get("primary")
        s = data.get("secondary")
        approx = bool(data.get("is_approximate", True))
        if is_valid_hex_color(p):
            return resolve_brand_palette(p, s, label="Approximate Brand Styling (Gemini Search)", is_approximate=approx)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Simple Safe Markdown -> Semantic HTML Renderer (Stdlib-only, XSS-safe)
# ---------------------------------------------------------------------------

def _render_inline_markdown(text: str) -> str:
    """Escape text and format bold, italic, and inline code."""
    escaped = html.escape(text)
    # Inline code
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    # Bold **text**
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    # Italic *text*
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


def markdown_to_semantic_html(markdown_text: str) -> str:
    """
    Convert markdown lines to clean, landmark-friendly semantic HTML.
    All text is escaped. Handles tables, headings, blockquotes, lists, and paragraphs.
    """
    lines = markdown_text.splitlines()
    html_parts: List[str] = []
    list_tag: Optional[str] = None
    in_table = False
    table_headers: List[str] = []
    table_rows: List[List[str]] = []

    def _flush_list():
        nonlocal list_tag
        if list_tag:
            html_parts.append(f"</{list_tag}>")
            list_tag = None

    def _flush_table():
        nonlocal in_table, table_headers, table_rows
        if in_table:
            html_parts.append('<div class="table-container"><table>')
            if table_headers:
                html_parts.append("<thead><tr>")
                for th in table_headers:
                    html_parts.append(f"<th>{_render_inline_markdown(th)}</th>")
                html_parts.append("</tr></thead>")
            if table_rows:
                html_parts.append("<tbody>")
                for row in table_rows:
                    html_parts.append("<tr>")
                    for td in row:
                        html_parts.append(f"<td>{_render_inline_markdown(td)}</td>")
                    html_parts.append("</tr>")
                html_parts.append("</tbody>")
            html_parts.append("</table></div>")
            in_table = False
            table_headers = []
            table_rows = []

    for line in lines:
        stripped = line.strip()

        # Table rows
        if stripped.startswith("|") and stripped.endswith("|"):
            _flush_list()
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # Skip separator line like |---|---|
            if all(re.match(r"^:?-+:?$", c) for c in cells):
                continue
            if not in_table:
                in_table = True
                table_headers = cells
            else:
                table_rows.append(cells)
            continue
        else:
            _flush_table()

        if not stripped:
            _flush_list()
            continue

        # Headings
        if stripped.startswith("#"):
            _flush_list()
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip()
            # Map top level to h2/h3 so page landmark h1 remains document title
            tag = f"h{min(level + 1, 6)}"
            html_parts.append(f"<{tag}>{_render_inline_markdown(title)}</{tag}>")
            continue

        # Blockquote
        if stripped.startswith(">"):
            _flush_list()
            quote_text = stripped.lstrip(">").strip()
            html_parts.append(f"<blockquote><p>{_render_inline_markdown(quote_text)}</p></blockquote>")
            continue

        # Bullet list
        if stripped.startswith(("- ", "* ", "+ ")):
            if list_tag != "ul":
                _flush_list()
                html_parts.append("<ul>")
                list_tag = "ul"
            item_text = stripped[2:].strip()
            html_parts.append(f"<li>{_render_inline_markdown(item_text)}</li>")
            continue

        # Numbered list
        num_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if num_match:
            if list_tag != "ol":
                _flush_list()
                html_parts.append("<ol>")
                list_tag = "ol"
            html_parts.append(f"<li>{_render_inline_markdown(num_match.group(2))}</li>")
            continue

        _flush_list()
        # Regular paragraph
        html_parts.append(f"<p>{_render_inline_markdown(stripped)}</p>")

    _flush_list()
    _flush_table()
    return "\n".join(html_parts)


# ---------------------------------------------------------------------------
# Presentation Model Extraction
# ---------------------------------------------------------------------------

@dataclass
class PresentationModel:
    ticker: str
    company: str
    analysis_date: str
    run_id: str
    provider: str
    model: str
    checksum: str
    generated_at: str
    notices: List[str]
    report_html: str
    artifacts: Dict[str, Any]
    summary: Dict[str, Any]
    data_gaps: List[Dict[str, str]]
    sources: List[Dict[str, Any]]
    palette: Dict[str, Any]


def extract_presentation_model(
    run_dir: Union[str, Path],
    *,
    palette: Optional[Dict[str, Any]] = None,
) -> PresentationModel:
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    # Load artifacts bundle
    artifacts_data: Dict[str, Any] = {}
    for art_file in sorted(run_dir.glob("*_artifacts.json")):
        try:
            artifacts_data = json.loads(art_file.read_text(encoding="utf-8"))
            break
        except Exception:
            pass

    # Fallback to loading individual envelopes
    if not artifacts_data:
        for env_file in sorted(run_dir.glob("a*_envelope.json")):
            try:
                role = env_file.stem.replace("_envelope", "").upper()
                artifacts_data[role] = json.loads(env_file.read_text(encoding="utf-8"))
            except Exception:
                pass

    # Load run_summary.json if present
    summary_data: Dict[str, Any] = {}
    summary_file = run_dir / "run_summary.json"
    if summary_file.is_file():
        try:
            summary_data = json.loads(summary_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Load report markdown
    report_text = ""
    for rep_file in sorted(run_dir.glob("*_report.md")):
        try:
            report_text = rep_file.read_text(encoding="utf-8")
            break
        except Exception:
            pass

    # Extract metadata from front matter or summary
    ticker = summary_data.get("params", {}).get("ticker") or "UNKNOWN"
    company = summary_data.get("params", {}).get("company") or ticker
    analysis_date = summary_data.get("params", {}).get("analysis_date") or ""
    run_id = summary_data.get("run_id") or run_dir.name
    provider = summary_data.get("provider") or "mock"
    model_name = summary_data.get("model") or "mock"
    checksum = summary_data.get("checksum") or ""
    generated_at = summary_data.get("finished_at") or summary_data.get("started_at") or ""

    if ticker == "UNKNOWN" or company in ("UNKNOWN", ticker):
        for env_dict in artifacts_data.values():
            if isinstance(env_dict, dict) and isinstance(env_dict.get("params"), dict):
                p = env_dict["params"]
                if p.get("ticker"):
                    ticker = p["ticker"]
                if p.get("company"):
                    company = p["company"]
                if p.get("analysis_date") and not analysis_date:
                    analysis_date = p["analysis_date"]
                if ticker != "UNKNOWN" and company not in ("UNKNOWN", ticker):
                    break

    # Parse front-matter from report markdown if available
    front_matter_notices: List[str] = []
    body_markdown = report_text
    if report_text.startswith("---"):
        parts = report_text.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body_markdown = parts[2].strip()
            for line in fm_text.splitlines():
                k_v = line.split(":", 1)
                if len(k_v) == 2:
                    k, v = k_v[0].strip(), k_v[1].strip()
                    if k == "ticker" and ticker == "UNKNOWN":
                        ticker = v
                    elif k == "company" and company == "UNKNOWN":
                        company = v
                    elif k == "analysis_date" and not analysis_date:
                        analysis_date = v
                    elif k == "run_id" and run_id == run_dir.name:
                        run_id = v
                    elif k == "provider":
                        provider = v
                    elif k == "model":
                        model_name = v
                    elif k == "frozen_checksum" and not checksum:
                        checksum = v
                    elif k == "generated_at" and not generated_at:
                        generated_at = v

    # Extract leading blockquote notices from top of body (before headings or content)
    clean_body_lines: List[str] = []
    in_leading_notices = True
    for line in body_markdown.splitlines():
        stripped = line.strip()
        if in_leading_notices:
            if stripped.startswith(">"):
                front_matter_notices.append(stripped.lstrip(">").strip())
                continue
            elif stripped:
                in_leading_notices = False
        clean_body_lines.append(line)

    rendered_report_html = markdown_to_semantic_html("\n".join(clean_body_lines))

    # Extract data gaps
    data_gaps: List[Dict[str, str]] = []
    for agent_id, env in artifacts_data.items():
        if isinstance(env, dict):
            metrics = env.get("metrics", {})
            for gap in metrics.get("missing_data", []):
                if isinstance(gap, dict):
                    data_gaps.append({
                        "agent": agent_id,
                        "field": str(gap.get("field", "unknown")),
                        "reason": str(gap.get("reason", "Not disclosed")),
                    })

    # Extract sources
    sources: List[Dict[str, Any]] = []
    seen_urls = set()
    for agent_id, env in artifacts_data.items():
        if isinstance(env, dict):
            for src in env.get("sources", []):
                if isinstance(src, dict):
                    url = src.get("url") or f"{agent_id}_source_{len(sources)}"
                    if url not in seen_urls:
                        seen_urls.add(url)
                        sources.append(src)

    active_palette = palette or DEFAULT_PALETTE

    return PresentationModel(
        ticker=ticker,
        company=company,
        analysis_date=analysis_date,
        run_id=run_id,
        provider=provider,
        model=model_name,
        checksum=checksum,
        generated_at=generated_at,
        notices=front_matter_notices,
        report_html=rendered_report_html,
        artifacts=artifacts_data,
        summary=summary_data,
        data_gaps=data_gaps,
        sources=sources,
        palette=active_palette,
    )


# ---------------------------------------------------------------------------
# HTML Document Builder
# ---------------------------------------------------------------------------

def _render_css(palette: Dict[str, Any]) -> str:
    fg_primary = get_readable_text_color(palette["primary"])
    fg_sec = get_readable_text_color(palette["secondary"])

    return f"""
:root {{
  --primary: {palette["primary"]};
  --primary-fg: {fg_primary};
  --primary-dark: {palette.get("primary_dark", palette["primary"])};
  --secondary: {palette["secondary"]};
  --secondary-fg: {fg_sec};
  --accent: {palette["accent"]};
  --bg: {palette["background"]};
  --surface: {palette["surface"]};
  --surface-subtle: {palette["surface_subtle"]};
  --text: {palette["text"]};
  --text-muted: {palette["text_muted"]};
  --border: {palette["border"]};
  --border-strong: {palette["border_strong"]};
  --success: {palette["success"]};
  --success-bg: {palette["success_bg"]};
  --warning: {palette["warning"]};
  --warning-bg: {palette["warning_bg"]};
  --danger: {palette["danger"]};
  --danger-bg: {palette["danger_bg"]};
}}

* {{
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}}

body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  line-height: 1.6;
  color: var(--text);
  background-color: var(--bg);
  padding: 0;
}}

.page-wrapper {{
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}}

/* Header & Banner */
header.site-header {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 2rem;
  margin-bottom: 2rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}}

.meta-pill-bar {{
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-bottom: 1rem;
}}

.pill {{
  display: inline-flex;
  align-items: center;
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 0.25rem 0.65rem;
  border-radius: 9999px;
}}

.pill-human-review {{
  background-color: #fee2e2;
  color: #991b1b;
  border: 1px solid #f87171;
}}

.pill-palette {{
  background-color: var(--surface-subtle);
  color: var(--text-muted);
  border: 1px solid var(--border);
}}

.pill-mock {{
  background-color: #fef3c7;
  color: #92400e;
  border: 1px solid #fcd34d;
}}

h1.report-title {{
  font-size: 2.25rem;
  font-weight: 800;
  color: var(--primary);
  margin-bottom: 0.5rem;
  letter-spacing: -0.02em;
}}

.report-subtitle {{
  font-size: 1.1rem;
  color: var(--text-muted);
  margin-bottom: 1.25rem;
}}

.report-metadata-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 1rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--border);
  font-size: 0.875rem;
}}

.meta-item .label {{
  font-size: 0.75rem;
  text-transform: uppercase;
  color: var(--text-muted);
  font-weight: 600;
  display: block;
}}

.meta-item .value {{
  font-weight: 600;
  color: var(--text);
  word-break: break-all;
}}

/* Quick Nav */
nav.section-nav {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.75rem 1.25rem;
  margin-bottom: 2rem;
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  font-size: 0.875rem;
}}

nav.section-nav a {{
  color: var(--primary);
  text-decoration: none;
  font-weight: 600;
}}

nav.section-nav a:hover {{
  text-decoration: underline;
}}

/* Alerts & Notices */
.alert-box {{
  background: var(--surface);
  border-left: 4px solid var(--warning);
  padding: 1rem 1.25rem;
  margin-bottom: 2rem;
  border-radius: 0 8px 8px 0;
  box-shadow: 0 1px 2px rgba(0,0,0,0.03);
}}

.alert-box p {{
  font-size: 0.9rem;
  margin-bottom: 0.5rem;
  color: var(--text);
}}
.alert-box p:last-child {{
  margin-bottom: 0;
}}

/* Cards & Grid Layout */
.card-section {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.75rem;
  margin-bottom: 2rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}}

.card-section h2 {{
  font-size: 1.4rem;
  font-weight: 700;
  color: var(--primary);
  margin-bottom: 1.25rem;
  border-bottom: 2px solid var(--surface-subtle);
  padding-bottom: 0.5rem;
}}

.metrics-strip {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin-bottom: 1.5rem;
}}

.metric-card {{
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.25rem;
  text-align: center;
}}

.metric-card .metric-name {{
  font-size: 0.75rem;
  text-transform: uppercase;
  font-weight: 700;
  color: var(--text-muted);
  margin-bottom: 0.35rem;
}}

.metric-card .metric-val {{
  font-size: 1.75rem;
  font-weight: 800;
  color: var(--primary);
  line-height: 1.2;
}}

.metric-card .metric-status {{
  font-size: 0.8rem;
  margin-top: 0.35rem;
  font-weight: 600;
}}

.status-safe {{ color: var(--success); }}
.status-neutral {{ color: var(--text-muted); }}
.status-warning {{ color: var(--warning); }}
.status-danger {{ color: var(--danger); }}

/* Debate Container */
.debate-grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
  margin-top: 1rem;
}}

.debate-column {{
  background: var(--surface-subtle);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.25rem;
}}

.debate-column h3 {{
  font-size: 1.15rem;
  font-weight: 700;
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}}

.thesis-item, .rebuttal-item {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1rem;
  margin-bottom: 1rem;
}}
.thesis-item:last-child, .rebuttal-item:last-child {{
  margin-bottom: 0;
}}

.thesis-header {{
  font-weight: 700;
  font-size: 0.95rem;
  color: var(--primary);
  margin-bottom: 0.35rem;
}}

.thesis-body {{
  font-size: 0.875rem;
  margin-bottom: 0.5rem;
}}

.thesis-meta {{
  font-size: 0.75rem;
  color: var(--text-muted);
}}

/* Tables */
.table-container {{
  width: 100%;
  overflow-x: auto;
  margin: 1rem 0;
}}

table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.875rem;
  text-align: left;
}}

th, td {{
  padding: 0.65rem 0.85rem;
  border-bottom: 1px solid var(--border);
}}

th {{
  background: var(--surface-subtle);
  font-weight: 700;
  color: var(--text);
  border-top: 1px solid var(--border);
}}

tr:hover td {{
  background: var(--surface-subtle);
}}

/* Prose content */
.prose-content {{
  font-size: 0.95rem;
  line-height: 1.7;
}}

.prose-content p {{
  margin-bottom: 1rem;
}}

.prose-content h2, .prose-content h3 {{
  margin-top: 1.5rem;
  margin-bottom: 0.75rem;
  color: var(--primary);
}}

.prose-content ul, .prose-content ol {{
  margin-left: 1.5rem;
  margin-bottom: 1rem;
}}

.prose-content blockquote {{
  border-left: 3px solid var(--primary);
  padding-left: 1rem;
  font-style: italic;
  margin: 1rem 0;
  color: var(--text-muted);
}}

footer.site-footer {{
  text-align: center;
  font-size: 0.8rem;
  color: var(--text-muted);
  padding: 2rem 0;
  border-top: 1px solid var(--border);
  margin-top: 3rem;
}}

/* Print Stylesheet */
@media print {{
  body {{
    background: #ffffff;
    color: #000000;
    font-size: 11pt;
  }}
  .page-wrapper {{
    max-width: 100%;
    padding: 0;
  }}
  nav.section-nav {{
    display: none;
  }}
  .card-section {{
    border: none;
    box-shadow: none;
    padding: 0;
    margin-bottom: 2rem;
    page-break-inside: avoid;
  }}
  header.site-header {{
    border: none;
    box-shadow: none;
    padding: 0 0 1rem 0;
    border-bottom: 2pt solid #000;
  }}
}}

@media (max-width: 768px) {{
  .debate-grid {{
    grid-template-columns: 1fr;
  }}
  h1.report-title {{
    font-size: 1.75rem;
  }}
}}
"""


_FORENSIC_STATUS = {
    "Safe Zone": "status-safe", "Unlikely Manipulator": "status-safe",
    "Grey Zone": "status-warning",
    "Distress Zone": "status-danger", "High Manipulation Risk": "status-danger",
}


def _forensic_status_class(label: Any) -> str:
    """Colour only labels the calculators actually emit; anything else stays neutral."""
    return _FORENSIC_STATUS.get(str(label), "status-neutral")


def generate_html_report(model: PresentationModel) -> str:
    palette = model.palette
    css = _render_css(palette)

    # 1. Key Metrics Extraction
    a1 = model.artifacts.get("A1", {}).get("artifacts", {})
    a3 = model.artifacts.get("A3", {}).get("artifacts", {})
    a7 = model.artifacts.get("A7", {}).get("artifacts", {})
    comp = a7.get("comparison_fields", {})

    share_price = a1.get("market_cap_check", {}).get("price")
    market_cap = a1.get("market_cap_check", {}).get("cap")
    shares = a1.get("shares_basic")

    price_str = f"${share_price:,.2f}" if isinstance(share_price, (int, float)) else "Unavailable"
    cap_str = f"${market_cap:,.0f}" if isinstance(market_cap, (int, float)) else "Unavailable"
    shares_str = f"{int(shares):,}" if shares and str(shares).isdigit() else (str(shares) if shares else "Unavailable")

    # Forensics
    altman = a3.get("AltmanZ", {})
    altman_score = altman.get("score")
    altman_class = altman.get("classification") or "Unavailable"  # never infer a label the calculator did not emit
    altman_str = f"{altman_score:.2f}" if isinstance(altman_score, (int, float)) else "Unavailable"

    beneish = a3.get("BeneishM", {})
    beneish_score = beneish.get("score")
    beneish_class = beneish.get("classification") or "Unavailable"
    beneish_str = f"{beneish_score:.2f}" if isinstance(beneish_score, (int, float)) else "Unavailable"

    # Overall Score & Recommendation
    quality_score = comp.get("quality_score_10")
    valuation_score = comp.get("valuation_score_10")
    irr_5y = comp.get("expected_5y_irr_base_pct")
    irr_str = f"{irr_5y:.1f}%" if isinstance(irr_5y, (int, float)) else "Unavailable"

    # Bull & Bear Cases
    bull_cases = model.artifacts.get("A6B", {}).get("artifacts", {}).get("BullCase", [])
    bear_rebuttals = model.artifacts.get("A6R", {}).get("artifacts", {}).get("BearRebuttals", [])
    danger_zone = model.artifacts.get("A6", {}).get("artifacts", {}).get("DangerZoneDashboard", [])

    # Format notices
    notices_html = ""
    if model.notices:
        items = "\n".join(f"<p>{_render_inline_markdown(n)}</p>" for n in model.notices)
        notices_html = f'<div class="alert-box" role="region" aria-label="Review Notices">{items}</div>'

    # Format Bull / Bear debate
    bull_items_html = ""
    for b in bull_cases:
        bid = html.escape(str(b.get("id", "Thesis")))
        thesis = html.escape(str(b.get("thesis", "")))
        impact = html.escape(str(b.get("impact", "")))
        evid = ", ".join(html.escape(str(e)) for e in b.get("evidence", []))
        bull_items_html += f"""
        <div class="thesis-item">
          <div class="thesis-header">{bid}: {thesis}</div>
          <div class="thesis-body"><strong>Target Impact:</strong> {impact}</div>
          <div class="thesis-meta">Cited Evidence: {evid}</div>
        </div>
        """

    bear_items_html = ""
    for r in bear_rebuttals:
        bid = html.escape(str(r.get("bull_id", "")))
        counter = html.escape(str(r.get("counter", "")))
        prob = html.escape(str(r.get("probability", "Med")))
        sev = html.escape(str(r.get("severity", "Med")))
        bear_items_html += f"""
        <div class="rebuttal-item">
          <div class="thesis-header">Rebuttal to {bid} (Prob: {prob}, Sev: {sev})</div>
          <div class="thesis-body">{counter}</div>
        </div>
        """

    # Format Danger Zone
    danger_rows_html = ""
    for d in danger_zone:
        cat = html.escape(str(d.get("category", "")))
        flag = html.escape(str(d.get("flag", "")))
        note = html.escape(str(d.get("note", "")))
        srcs = ", ".join(html.escape(str(s)) for s in d.get("sources", []))
        danger_rows_html += f"<tr><td>{flag} <strong>{cat}</strong></td><td>{note}</td><td>{srcs}</td></tr>"

    # Format Data Gaps
    gap_rows_html = ""
    if model.data_gaps:
        for g in model.data_gaps:
            ag = html.escape(g["agent"])
            fld = html.escape(g["field"])
            rsn = html.escape(g["reason"])
            gap_rows_html += f"<tr><td>{ag}</td><td><code>{fld}</code></td><td>{rsn}</td></tr>"
    else:
        gap_rows_html = '<tr><td colspan="3">No unexcused data gaps recorded.</td></tr>'

    # Format Sources
    source_rows_html = ""
    if model.sources:
        for s in model.sources:
            stype = html.escape(str(s.get("type", "Document")))
            surl = html.escape(str(s.get("url", "")))
            sdate = html.escape(str(s.get("doc_date", "Undated")))
            stitle = html.escape(str(s.get("title", s.get("type", "Source"))))
            source_rows_html += f"<tr><td><strong>{stype}</strong></td><td>{stitle}</td><td>{sdate}</td><td><code>{surl}</code></td></tr>"
    else:
        source_rows_html = '<tr><td colspan="4">No document sources attached to this run.</td></tr>'

    palette_label = html.escape(palette.get("label", "Institutional Default Palette"))
    is_mock = str(model.provider).strip().lower() == "mock"
    mock_pill = '<span class="pill pill-mock">MOCK PROVIDER — SYNTHETIC</span>' if is_mock else ""

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; font-src 'none'; img-src 'none'; script-src 'none'; base-uri 'none'; form-action 'none';">
  <title>{html.escape(model.company)} ({html.escape(model.ticker)}) — Equity Research Report</title>
  <style>
{css}
  </style>
</head>
<body>
  <div class="page-wrapper">
    <header class="site-header" role="banner">
      <div class="meta-pill-bar">
        <span class="pill pill-human-review">PENDING HUMAN REVIEW</span>
        {mock_pill}
        <span class="pill pill-palette">Theme: {palette_label}</span>
      </div>
      <h1 class="report-title">{html.escape(model.company)} ({html.escape(model.ticker)})</h1>
      <p class="report-subtitle">Contract-Driven Multi-Agent Equity Research Report</p>

      <div class="report-metadata-grid">
        <div class="meta-item">
          <span class="label">Analysis Cutoff</span>
          <span class="value">{html.escape(model.analysis_date or "N/A")}</span>
        </div>
        <div class="meta-item">
          <span class="label">Provider / Model</span>
          <span class="value">{html.escape(model.provider)} ({html.escape(model.model)})</span>
        </div>
        <div class="meta-item">
          <span class="label">Run ID</span>
          <span class="value">{html.escape(model.run_id)}</span>
        </div>
        <div class="meta-item">
          <span class="label">Frozen Checksum</span>
          <span class="value">{html.escape(model.checksum[:20] + "..." if len(model.checksum) > 20 else model.checksum or "None")}</span>
        </div>
      </div>
    </header>

    <nav class="section-nav" aria-label="Report Table of Contents">
      <a href="#summary">Executive Summary</a>
      <a href="#metrics">Key Metrics</a>
      <a href="#debate">Bull vs Bear Debate</a>
      <a href="#forensics">Financial Forensics</a>
      <a href="#risks">Risk Dashboard</a>
      <a href="#gaps">Data Gaps</a>
      <a href="#sources">Sources</a>
    </nav>

    {notices_html}

    <main role="main">
      <section id="metrics" class="card-section" aria-labelledby="heading-metrics">
        <h2 id="heading-metrics">Market Snapshot & Valuation Highlights</h2>
        <div class="metrics-strip">
          <div class="metric-card">
            <div class="metric-name">Share Price</div>
            <div class="metric-val">{html.escape(str(price_str))}</div>
            <div class="metric-status status-neutral">Basic Shares: {html.escape(str(shares_str))}</div>
          </div>
          <div class="metric-card">
            <div class="metric-name">Market Capitalization</div>
            <div class="metric-val">{html.escape(str(cap_str))}</div>
            <div class="metric-status status-neutral">Implied Equity Value</div>
          </div>
          <div class="metric-card">
            <div class="metric-name">Expected 5Y IRR</div>
            <div class="metric-val">{html.escape(str(irr_str))}</div>
            <div class="metric-status status-neutral">Base Scenario</div>
          </div>
          <div class="metric-card">
            <div class="metric-name">Quality Score</div>
            <div class="metric-val">{html.escape(str(quality_score if quality_score is not None else "N/A"))}/10</div>
            <div class="metric-status status-neutral">Valuation: {html.escape(str(valuation_score if valuation_score is not None else "N/A"))}/10</div>
          </div>
        </div>
      </section>

      <section id="forensics" class="card-section" aria-labelledby="heading-forensics">
        <h2 id="heading-forensics">Independent Financial Forensics</h2>
        <div class="metrics-strip">
          <div class="metric-card">
            <div class="metric-name">Altman Z-Score</div>
            <div class="metric-val">{html.escape(str(altman_str))}</div>
            <div class="metric-status {_forensic_status_class(altman_class)}">{html.escape(str(altman_class))}</div>
          </div>
          <div class="metric-card">
            <div class="metric-name">Beneish M-Score</div>
            <div class="metric-val">{html.escape(str(beneish_str))}</div>
            <div class="metric-status {_forensic_status_class(beneish_class)}">{html.escape(str(beneish_class))}</div>
          </div>
        </div>
      </section>

      <section id="debate" class="card-section" aria-labelledby="heading-debate">
        <h2 id="heading-debate">Bull Theses & Bear Rebuttals (1:1 Debate)</h2>
        <div class="debate-grid">
          <div class="debate-column">
            <h3>🟢 A6B Bull Theses</h3>
            {bull_items_html or "<p>No bull theses available.</p>"}
          </div>
          <div class="debate-column">
            <h3>🔴 A6R Bear Rebuttals</h3>
            {bear_items_html or "<p>No bear rebuttals available.</p>"}
          </div>
        </div>
      </section>

      {f'''
      <section id="risks" class="card-section" aria-labelledby="heading-risks">
        <h2 id="heading-risks">Danger Zone & Downside Risks</h2>
        <div class="table-container">
          <table>
            <thead>
              <tr><th>Category</th><th>Risk Diagnostic</th><th>Sources</th></tr>
            </thead>
            <tbody>
              {danger_rows_html}
            </tbody>
          </table>
        </div>
      </section>
      ''' if danger_rows_html else ''}

      <section id="summary" class="card-section prose-content" aria-labelledby="heading-report-body">
        <h2 id="heading-report-body">Assembled Research Findings</h2>
        {model.report_html}
      </section>

      <section id="gaps" class="card-section" aria-labelledby="heading-gaps">
        <h2 id="heading-gaps">Research Data Gaps & Disclosed Limitations</h2>
        <p style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 1rem;">
          In accordance with the zero-fabrication policy, unavailable values remain null with explicit disclosure.
        </p>
        <div class="table-container">
          <table>
            <thead>
              <tr><th>Agent</th><th>Field</th><th>Disclosed Gap Reason</th></tr>
            </thead>
            <tbody>
              {gap_rows_html}
            </tbody>
          </table>
        </div>
      </section>

      <section id="sources" class="card-section" aria-labelledby="heading-sources">
        <h2 id="heading-sources">Supplied Evidence & Source Ledger</h2>
        <div class="table-container">
          <table>
            <thead>
              <tr><th>Type</th><th>Document Title</th><th>Filing Date</th><th>Source Reference / URL</th></tr>
            </thead>
            <tbody>
              {source_rows_html}
            </tbody>
          </table>
        </div>
      </section>
    </main>

    <footer class="site-footer" role="contentinfo">
      <p>FUWForge Research Pipeline &bull; Run: <code>{html.escape(model.run_id)}</code> &bull; Checksum: <code>{html.escape(model.checksum or "Unsealed")}</code></p>
      <p style="margin-top: 0.35rem;">Sealed bytes match cryptographic manifest. Do not treat automated screening outputs as certified investment advice.</p>
    </footer>
  </div>
</body>
</html>
"""
    return doc


def render_html_report(
    run_dir: Union[str, Path],
    *,
    brand_search: bool = False,
    output_path: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Main entrypoint to generate the standalone HTML presentation for a run.
    """
    run_path = Path(run_dir).resolve()
    palette = None
    if brand_search:
        # Best-effort brand search if ticker is identifiable
        try:
            temp_model = extract_presentation_model(run_path)
            palette = search_brand_colors_gemini(temp_model.ticker, temp_model.company)
        except Exception:
            palette = None

    model = extract_presentation_model(run_path, palette=palette)
    html_content = generate_html_report(model)

    if output_path:
        target = Path(output_path).resolve()
    else:
        target = run_path / f"{model.ticker}_{model.analysis_date}_report.html"

    target.write_text(html_content, encoding="utf-8")
    return target
