"""HTML for e-mails: table layout with inline styles only, so Outlook, Gmail and mobile clients
render it the same. Colours are the application's design tokens (light theme)."""

from html import escape

BRAND = "#8b1e3f"
TEXT, MUTED, BORDER, SURFACE, BG = "#1b1b1b", "#6b6a66", "#e3e2dd", "#ffffff", "#f4f4f1"
STATUS = {  # fill, text, background
    "good": ("#0ca30c", "#006300", "#e3f4e3"),
    "warning": ("#fab219", "#8a5a00", "#fdf1d6"),
    "serious": ("#ec835a", "#a6431c", "#fbe6dc"),
    "critical": ("#d03b3b", "#b02a2a", "#f9e0e0"),
    "info": ("#2a78d6", "#1d5aa6", "#e3eefb"),
    "neutral": ("#9a9993", "#52514e", "#efeeea"),
}
FONT = "font-family:'Segoe UI',Arial,Helvetica,sans-serif"


def n(value) -> str:
    """Thousands with a non-breaking space (1 234 567)."""
    return f"{int(value or 0):,}".replace(",", " ")


def badge(text: str, status: str = "neutral") -> str:
    _, fg, bg = STATUS[status]
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:{bg};color:{fg};'
            f'font-size:12px;font-weight:600;white-space:nowrap">{escape(str(text))}</span>')


def rate_status(pct) -> str:
    if pct is None:
        return "neutral"
    return "good" if pct >= 98 else "warning" if pct >= 80 else "critical"


def bar(pct, status: str | None = None, width: int = 110) -> str:
    """Horizontal bar as two table cells – works without CSS support for widths in percent."""
    if pct is None:
        return f'<span style="color:{MUTED}">–</span>'
    fill = STATUS[status or rate_status(pct)][0]
    on = max(1, min(width, round(width * pct / 100)))
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse"><tr>'
            f'<td style="width:{width}px"><table role="presentation" cellpadding="0" cellspacing="0" width="{width}" '
            f'style="border-collapse:collapse;background:{BORDER};border-radius:4px"><tr>'
            f'<td width="{on}" height="8" style="background:{fill};border-radius:4px;font-size:0;line-height:0">&nbsp;</td>'
            f'<td style="font-size:0;line-height:0">&nbsp;</td></tr></table></td>'
            f'<td style="padding-left:8px;font-size:13px;color:{TEXT};white-space:nowrap">{pct:.1f}&nbsp;%</td></tr></table>')


def kpi(label: str, value: str, foot: str = "", status: str | None = None) -> str:
    color = STATUS[status][1] if status else TEXT
    return (f'<td valign="top" style="padding:14px 16px;background:{SURFACE};border:1px solid {BORDER};border-radius:10px">'
            f'<div style="font-size:12px;color:{MUTED}">{escape(label)}</div>'
            f'<div style="font-size:24px;font-weight:700;color:{color};margin-top:2px">{value}</div>'
            f'<div style="font-size:12px;color:{MUTED};margin-top:2px">{foot}</div></td>')


def kpi_row(tiles: list[str]) -> str:
    gap = '<td style="width:10px">&nbsp;</td>'
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:separate">'
            f'<tr>{gap.join(tiles)}</tr></table>')


def section(title: str, body: str) -> str:
    return (f'<h3 style="margin:26px 0 10px;font-size:16px;color:{TEXT}">{escape(title)}</h3>'
            f'<div style="background:{SURFACE};border:1px solid {BORDER};border-radius:10px;padding:4px 0">{body}</div>')


def table(head: list[str], rows: list[list[str]], align: list[str] | None = None) -> str:
    align = align or ["left"] * len(head)
    th = "".join(f'<th align="{a}" style="padding:8px 12px;font-size:11px;letter-spacing:.04em;text-transform:uppercase;'
                 f'color:{MUTED};border-bottom:1px solid {BORDER}">{escape(h)}</th>' for h, a in zip(head, align, strict=True))
    tr = "".join(
        "<tr>" + "".join(f'<td align="{a}" style="padding:8px 12px;font-size:13px;color:{TEXT};border-bottom:1px solid {BG}">{c}</td>'
                         for c, a in zip(r, align, strict=True)) + "</tr>"
        for r in rows)
    header = f"<tr>{th}</tr>" if any(head) else ""
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">{header}{tr}</table>'


def event_list(items: list[tuple[str, str]]) -> str:
    """[(status, text)] as a list with a coloured marker per line."""
    rows = "".join(
        f'<tr><td valign="top" style="padding:9px 0 9px 14px;width:14px"><div style="width:10px;height:10px;border-radius:5px;'
        f'background:{STATUS[s][0]};margin-top:4px"></div></td>'
        f'<td style="padding:8px 14px 8px 8px;font-size:14px;color:{TEXT};border-bottom:1px solid {BG}">{escape(t)}</td></tr>'
        for s, t in items)
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">{rows}</table>'


def page(title: str, subtitle: str, body: str, app_name: str, base_url: str, footer: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:{BG}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};{FONT}"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:760px;border-collapse:collapse">
<tr><td style="background:{BRAND};border-radius:12px 12px 0 0;padding:20px 24px;{FONT}">
  <div style="font-size:13px;color:#f3d7df;letter-spacing:.04em">{escape(app_name)} · DMARC</div>
  <div style="font-size:22px;font-weight:700;color:#ffffff;margin-top:4px">{escape(title)}</div>
  <div style="font-size:13px;color:#f3d7df;margin-top:4px">{escape(subtitle)}</div>
</td></tr>
<tr><td style="background:{BG};padding:18px 0 4px;{FONT}">{body}</td></tr>
<tr><td style="padding:22px 4px;font-size:12px;color:{MUTED};{FONT}">{footer}
  · <a href="{escape(base_url)}" style="color:{BRAND};font-weight:600;text-decoration:none">{escape(app_name)}</a></td></tr>
</table></td></tr></table></body></html>"""
