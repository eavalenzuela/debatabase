"""FastAPI app for the debatabase v0 search/review UI.

Routes:
  GET /                  → search page
  GET /search            → search results (HTMX-driven; full page or fragment)
  GET /cards/{id}        → card detail with mode toggle
  GET /tags              → vocabulary browse
  GET /tags/{slug}       → cards filtered by tag
  GET /sources/{id}      → cards from a single source
  GET /analyticals       → list analyticals
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, text as sqltext
from sqlalchemy.orm import joinedload

from debatabase.db import session_scope
from debatabase.models import (
    Analytical,
    Card,
    CardContentTag,
    ContentTag,
    Source,
)
from debatabase.web.render import RenderMode, render_card, snippet

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
templates.env.globals["render_card"] = render_card
templates.env.globals["snippet"] = snippet

app = FastAPI(title="debatabase")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

PAGE_SIZE = 50


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _tag_tree(session) -> list[dict]:
    """Return [{id, slug, label, n_cards, n_anal, children: [...]}, ...] in usage-desc order."""
    rows = session.execute(sqltext("""
        SELECT
          ct.id, ct.slug, ct.label, ct.parent_id,
          COALESCE(c.n, 0) AS n_cards,
          COALESCE(a.n, 0) AS n_analyticals
        FROM content_tags ct
        LEFT JOIN (SELECT content_tag_id, count(*) AS n FROM card_content_tags GROUP BY content_tag_id) c
          ON c.content_tag_id = ct.id
        LEFT JOIN (SELECT content_tag_id, count(*) AS n FROM analytical_content_tags GROUP BY content_tag_id) a
          ON a.content_tag_id = ct.id
        ORDER BY (COALESCE(c.n,0) + COALESCE(a.n,0)) DESC, ct.slug
    """)).all()

    by_id: dict[int, dict] = {}
    for r in rows:
        by_id[r.id] = {
            "id": r.id, "slug": r.slug, "label": r.label,
            "parent_id": r.parent_id,
            "n_cards": r.n_cards, "n_analyticals": r.n_analyticals,
            "children": [],
        }
    roots: list[dict] = []
    for node in by_id.values():
        if node["parent_id"] and node["parent_id"] in by_id:
            by_id[node["parent_id"]]["children"].append(node)
        else:
            roots.append(node)
    # Re-sort: root tags by total usage desc; children alphabetical
    roots.sort(key=lambda n: -(n["n_cards"] + n["n_analyticals"]))
    for n in by_id.values():
        n["children"].sort(key=lambda c: c["slug"])
    return roots


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return _search(request, q=None, tag=None, page=1)


@app.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    return _search(request, q=q, tag=tag, page=page)


def _search(request: Request, q: str | None, tag: str | None, page: int):
    offset = (page - 1) * PAGE_SIZE
    with session_scope() as s:
        # Base card-fetch query
        params: dict = {}

        # Build WHERE clauses for cards
        where_clauses = []
        if q:
            # tsvector match OR substring match against author_last/cite_short
            where_clauses.append(sqltext("""(
              cards.search_tsv @@ websearch_to_tsquery('english', :q)
              OR sources.author_last ILIKE :q_like
              OR sources.cite_short ILIKE :q_like
            )"""))
            params["q"] = q
            params["q_like"] = f"%{q}%"
        if tag:
            tag_id_row = s.execute(
                select(ContentTag.id).where(ContentTag.slug == tag)
            ).scalar_one_or_none()
            if tag_id_row is not None:
                where_clauses.append(
                    Card.id.in_(
                        select(CardContentTag.card_id)
                        .where(CardContentTag.content_tag_id == tag_id_row)
                    )
                )

        # Card list query
        card_stmt = (
            select(Card)
            .join(Source, Source.id == Card.source_id)
            .options(joinedload(Card.source))
        )
        # Card count query — same WHERE, no JOIN needed if we wrap
        count_stmt = (
            select(func.count(Card.id))
            .join(Source, Source.id == Card.source_id)
        )
        for clause in where_clauses:
            card_stmt = card_stmt.where(clause)
            count_stmt = count_stmt.where(clause)

        # Order: tsvector rank when query is present; else by id desc
        if q:
            card_stmt = card_stmt.order_by(
                sqltext("ts_rank(cards.search_tsv, websearch_to_tsquery('english', :q)) DESC, cards.id DESC")
            )
        else:
            card_stmt = card_stmt.order_by(Card.id.desc())

        card_stmt = card_stmt.params(**params).limit(PAGE_SIZE).offset(offset)
        count_stmt = count_stmt.params(**params)

        cards = s.execute(card_stmt).scalars().unique().all()
        total_cards_matching = s.execute(count_stmt).scalar_one()

        # Analyticals (only when query present, no pagination — typically few)
        analyticals = []
        if q:
            anal_stmt = (
                select(Analytical)
                .where(sqltext("search_tsv @@ websearch_to_tsquery('english', :q)"))
                .order_by(
                    sqltext("ts_rank(search_tsv, websearch_to_tsquery('english', :q)) DESC")
                )
                .params(q=q)
                .limit(20)
            )
            analyticals = s.execute(anal_stmt).scalars().all()

        # Tags per card for chips
        tags_by_card: dict[int, list[tuple[str, str]]] = {}
        if cards:
            card_ids = [c.id for c in cards]
            tag_rows = s.execute(
                select(CardContentTag.card_id, ContentTag.slug, ContentTag.label)
                .join(ContentTag, ContentTag.id == CardContentTag.content_tag_id)
                .where(CardContentTag.card_id.in_(card_ids))
            ).all()
            for cid, slug, label in tag_rows:
                tags_by_card.setdefault(cid, []).append((slug, label))

        # Counts for header
        n_cards = s.execute(select(func.count(Card.id))).scalar_one()
        n_anal = s.execute(select(func.count(Analytical.id))).scalar_one()
        n_sources = s.execute(select(func.count(Source.id))).scalar_one()
        n_tags = s.execute(select(func.count(ContentTag.id))).scalar_one()

        # Tag tree for sidebar
        tag_tree = _tag_tree(s)

    # Pagination math
    total_pages = max(1, (total_cards_matching + PAGE_SIZE - 1) // PAGE_SIZE)
    start_idx = offset + 1 if cards else 0
    end_idx = offset + len(cards)

    template = "search_results.html" if _is_htmx(request) else "search.html"
    return templates.TemplateResponse(
        request,
        template,
        {
            "q": q or "",
            "tag": tag or "",
            "cards": cards,
            "analyticals": analyticals,
            "tags_by_card": tags_by_card,
            "tag_tree": tag_tree,
            "n_cards": n_cards,
            "n_anal": n_anal,
            "n_sources": n_sources,
            "n_tags": n_tags,
            "total_cards_matching": total_cards_matching,
            "page": page,
            "total_pages": total_pages,
            "start_idx": start_idx,
            "end_idx": end_idx,
            "page_size": PAGE_SIZE,
        },
    )


# ---------------------------------------------------------------------------
# Card detail
# ---------------------------------------------------------------------------

@app.get("/cards/{card_id}", response_class=HTMLResponse)
def card_detail(
    request: Request,
    card_id: int,
    mode: RenderMode = Query(default="full"),
):
    with session_scope() as s:
        card = s.get(Card, card_id, options=[joinedload(Card.source)])
        if card is None:
            return HTMLResponse(f"Card {card_id} not found", status_code=404)
        tag_rows = s.execute(
            select(ContentTag.slug, ContentTag.label, CardContentTag.status)
            .join(CardContentTag, CardContentTag.content_tag_id == ContentTag.id)
            .where(CardContentTag.card_id == card_id)
        ).all()

    has_highlight = any(s["kind"] == "highlight" for s in (card.markup or []))
    has_underline = any(s["kind"] == "underline" for s in (card.markup or []))

    return templates.TemplateResponse(
        request,
        "card_detail.html",
        {
            "card": card,
            "tags": tag_rows,
            "mode": mode,
            "has_highlight": has_highlight,
            "has_underline": has_underline,
        },
    )


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@app.get("/tags", response_class=HTMLResponse)
def tags_index(request: Request):
    with session_scope() as s:
        rows = s.execute(sqltext("""
            SELECT
              ct.id, ct.slug, ct.label, ct.parent_id,
              COALESCE(c.n, 0) AS n_cards,
              COALESCE(a.n, 0) AS n_analyticals
            FROM content_tags ct
            LEFT JOIN (SELECT content_tag_id, count(*) AS n FROM card_content_tags GROUP BY content_tag_id) c
              ON c.content_tag_id = ct.id
            LEFT JOIN (SELECT content_tag_id, count(*) AS n FROM analytical_content_tags GROUP BY content_tag_id) a
              ON a.content_tag_id = ct.id
            ORDER BY (COALESCE(c.n,0) + COALESCE(a.n,0)) DESC, ct.slug
        """)).all()
    return templates.TemplateResponse(
        request, "tags_index.html", {"tags": rows}
    )


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

@app.get("/sources/{source_id}", response_class=HTMLResponse)
def source_detail(request: Request, source_id: int):
    with session_scope() as s:
        source = s.get(Source, source_id)
        if source is None:
            return HTMLResponse(f"Source {source_id} not found", status_code=404)
        cards = s.execute(
            select(Card).where(Card.source_id == source_id).order_by(Card.id)
        ).scalars().all()
    return templates.TemplateResponse(
        request, "source_detail.html",
        {"source": source, "cards": cards},
    )


# ---------------------------------------------------------------------------
# Analyticals
# ---------------------------------------------------------------------------

@app.get("/analyticals", response_class=HTMLResponse)
def analyticals_index(request: Request):
    with session_scope() as s:
        rows = s.execute(select(Analytical).order_by(Analytical.id)).scalars().all()
    return templates.TemplateResponse(
        request, "analyticals_index.html", {"analyticals": rows}
    )
