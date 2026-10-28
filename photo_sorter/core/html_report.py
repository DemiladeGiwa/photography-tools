"""Self-contained HTML contact sheet / proof sheet (works offline, shareable with clients)."""

from __future__ import annotations

import base64
import html
import time
from pathlib import Path
from typing import Optional, Sequence

from . import image_loader
from .library import FLAG_PICK, FLAG_REJECT, Library, Photo

_CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--mute:#6b6b70;--line:#e3e1dc;--pick:#2f9e44;--rej:#d6336c;--acc:#3b5bdb}
@media (prefers-color-scheme: dark){:root{--bg:#141416;--card:#1e1e21;--ink:#ececec;--mute:#9a9aa0;--line:#2c2c30}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:28px 32px 12px}h1{margin:0 0 4px;font-size:22px;font-weight:650}.sub{color:var(--mute)}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0 4px}.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 14px}
.stat b{display:block;font-size:18px}.stat span{color:var(--mute);font-size:12px}
.bar{position:sticky;top:0;background:var(--bg);padding:10px 32px;border-bottom:1px solid var(--line);display:flex;gap:6px;flex-wrap:wrap;z-index:2}
.bar button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:6px 14px;cursor:pointer;font:inherit}
.bar button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;padding:18px 32px 40px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.card.pick{outline:2px solid var(--pick)}.card.reject{opacity:.55}
.img{aspect-ratio:3/2;background:#0001;display:flex;align-items:center;justify-content:center}
.img img{max-width:100%;max-height:100%;display:block}
.meta{padding:9px 11px 11px}.name{font-weight:600;font-size:13px;word-break:break-all}
.row{display:flex;justify-content:space-between;color:var(--mute);font-size:12px;margin-top:3px}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;margin-right:4px;color:#fff}
.t-pick{background:var(--pick)}.t-rej{background:var(--rej)}.t-best{background:var(--acc)}
.stars{color:#f59f00;letter-spacing:1px}
footer{color:var(--mute);padding:0 32px 30px;font-size:12px}
"""

_JS = """
const btns=[...document.querySelectorAll('.bar button')];
btns.forEach(b=>b.onclick=()=>{btns.forEach(x=>x.classList.remove('on'));b.classList.add('on');
const f=b.dataset.f;document.querySelectorAll('.card').forEach(c=>{
c.style.display=(f==='all'||c.dataset[f]==='1')?'':'none'})});
"""


def _thumb_uri(lib: Optional[Library], p: Photo, max_dim: int) -> str:
    data = lib.thumbnail_bytes(p) if lib is not None else None
    if data is None:
        bgr, _ = image_loader.load_bgr(p.display, max_dim)
        data = image_loader.encode_jpeg(bgr, 78) if bgr is not None else None
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii") if data else ""


def generate_html_report(lib: Library, photos: Sequence[Photo], output_path: Path,
                         title: str = "Contact Sheet", embed_thumbnails: bool = True, max_dim: int = 360) -> Path:
    picks = sum(1 for p in photos if p.flag == FLAG_PICK)
    rejects = sum(1 for p in photos if p.flag == FLAG_REJECT)
    rated = sum(1 for p in photos if p.rating)
    groups = len({p.group_id for p in photos if p.group_id})
    cards = []
    for p in photos:
        cls = "pick" if p.flag == FLAG_PICK else ("reject" if p.flag == FLAG_REJECT else "")
        tags = ""
        if p.flag == FLAG_PICK:
            tags += '<span class="tag t-pick">Pick</span>'
        if p.flag == FLAG_REJECT:
            tags += '<span class="tag t-rej">Reject</span>'
        if p.is_best:
            tags += '<span class="tag t-best">Best of group</span>'
        stars = "★" * p.rating
        img = f'<img loading="lazy" src="{_thumb_uri(lib, p, max_dim)}" alt="">' if embed_thumbnails else ""
        exif = html.escape(p.meta.format_exif_summary()) if p.meta else ""
        when = p.capture_time.strftime("%Y-%m-%d %H:%M") if p.capture_time else ""
        q = f"Q {p.quality_score:.0f}" if p.quality else ""
        cards.append(
            f'<div class="card {cls}" data-pick="{int(p.flag == FLAG_PICK)}" data-reject="{int(p.flag == FLAG_REJECT)}" '
            f'data-rated="{int(bool(p.rating))}" data-best="{int(p.is_best)}">'
            f'<div class="img">{img}</div><div class="meta"><div class="name">{html.escape(p.name)}</div>'
            f'<div class="row"><span>{tags}<span class="stars">{stars}</span></span><span>{q}</span></div>'
            f'<div class="row"><span>{html.escape(when)}</span><span>{html.escape(p.type_label)}</span></div>'
            f'<div class="row"><span>{exif}</span></div></div></div>'
        )
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>{_CSS}</style></head><body>
<header><h1>{html.escape(title)}</h1><div class="sub">{html.escape(str(lib.root.name if lib else ''))} · generated {time.strftime('%Y-%m-%d %H:%M')}</div>
<div class="stats"><div class="stat"><b>{len(photos)}</b><span>photos</span></div>
<div class="stat"><b>{picks}</b><span>picks</span></div><div class="stat"><b>{rejects}</b><span>rejects</span></div>
<div class="stat"><b>{rated}</b><span>rated</span></div><div class="stat"><b>{groups}</b><span>groups</span></div></div></header>
<div class="bar"><button class="on" data-f="all">All</button><button data-f="pick">Picks</button>
<button data-f="rated">Rated</button><button data-f="best">Best of groups</button><button data-f="reject">Rejects</button></div>
<div class="grid">{''.join(cards)}</div>
<footer>Made with Photography Tools Suite</footer><script>{_JS}</script></body></html>"""
    output_path = Path(output_path)
    output_path.write_text(doc, encoding="utf-8")
    return output_path
