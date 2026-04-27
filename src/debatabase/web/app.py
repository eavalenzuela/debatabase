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

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, text as sqltext
from sqlalchemy.orm import Session, joinedload

from debatabase.db import session_scope
from debatabase.docx_export import (
    ExportAnalytical,
    ExportCard,
    ExportEntry,
    render_workspace_to_docx,
)
from debatabase.models import (
    Analytical,
    Card,
    CardContentTag,
    ContentTag,
    Source,
    Workspace,
    WorkspaceEntry,
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


# ---------------------------------------------------------------------------
# Workspace
#
# Each user has exactly one current workspace (UNIQUE on workspaces.user_id).
# v1 hardcodes user_id=1 (the bootstrap "local" user); FEATURE_ADDITIONS.md #6
# replaces get_current_user_id() with a real session lookup.
# ---------------------------------------------------------------------------

def get_current_user_id() -> int:
    return 1


def _get_workspace(s: Session, user_id: int) -> Workspace:
    ws = s.scalar(select(Workspace).where(Workspace.user_id == user_id))
    if ws is None:
        ws = Workspace(user_id=user_id)
        s.add(ws)
        s.flush()
    return ws


def _next_position(s: Session, workspace_id: int) -> int:
    cur = s.scalar(
        select(func.coalesce(func.max(WorkspaceEntry.position), 0)).where(
            WorkspaceEntry.workspace_id == workspace_id
        )
    )
    return int(cur or 0) + 1


def _parse_header_path(raw: str | None) -> list[str]:
    """Accept ' > ' (UI display), '›' (compact), or comma as separators."""
    if not raw or not raw.strip():
        return []
    for sep in (" > ", " › ", "›", ","):
        if sep in raw:
            return [p.strip() for p in raw.split(sep) if p.strip()]
    return [raw.strip()]


def _load_entries(s: Session, workspace_id: int) -> list[WorkspaceEntry]:
    return list(
        s.execute(
            select(WorkspaceEntry)
            .where(WorkspaceEntry.workspace_id == workspace_id)
            .options(
                joinedload(WorkspaceEntry.card).joinedload(Card.source),
                joinedload(WorkspaceEntry.analytical),
            )
            .order_by(WorkspaceEntry.position)
        ).scalars().unique().all()
    )


def _group_entries(
    entries: list[WorkspaceEntry],
) -> list[tuple[list[str], list[WorkspaceEntry]]]:
    """Group consecutive entries that share a header_path, preserving order."""
    groups: list[tuple[list[str], list[WorkspaceEntry]]] = []
    for e in entries:
        path = list(e.header_path or [])
        if not groups or groups[-1][0] != path:
            groups.append((path, []))
        groups[-1][1].append(e)
    return groups


def _entries_fragment(request: Request, user_id: int) -> HTMLResponse:
    with session_scope() as s:
        ws = _get_workspace(s, user_id)
        entries = _load_entries(s, ws.id)
    return templates.TemplateResponse(
        request,
        "_workspace_entries.html",
        {"groups": _group_entries(entries), "n_entries": len(entries)},
    )


@app.get("/workspace", response_class=HTMLResponse)
def workspace_view(
    request: Request, user_id: int = Depends(get_current_user_id)
):
    with session_scope() as s:
        ws = _get_workspace(s, user_id)
        entries = _load_entries(s, ws.id)
    return templates.TemplateResponse(
        request,
        "workspace.html",
        {
            "groups": _group_entries(entries),
            "n_entries": len(entries),
        },
    )


@app.post("/workspace/entries", response_class=HTMLResponse)
def add_workspace_entry(
    request: Request,
    card_id: int | None = Form(default=None),
    analytical_id: int | None = Form(default=None),
    header_path: str | None = Form(default=None),
    user_id: int = Depends(get_current_user_id),
):
    if (card_id is None) == (analytical_id is None):
        return HTMLResponse(
            "must specify exactly one of card_id / analytical_id",
            status_code=400,
        )
    with session_scope() as s:
        ws = _get_workspace(s, user_id)
        entry = WorkspaceEntry(
            workspace_id=ws.id,
            position=_next_position(s, ws.id),
            header_path=_parse_header_path(header_path) or None,
            card_id=card_id,
            analytical_id=analytical_id,
        )
        s.add(entry)
    # HTMX feedback: small confirmation. Caller decides where to swap it in.
    return HTMLResponse(
        '<span class="ws-added">added · '
        '<a href="/workspace">view workspace</a></span>'
    )


@app.delete("/workspace/entries/{entry_id}", response_class=HTMLResponse)
def delete_workspace_entry(
    request: Request,
    entry_id: int,
    user_id: int = Depends(get_current_user_id),
):
    with session_scope() as s:
        ws = _get_workspace(s, user_id)
        entry = s.get(WorkspaceEntry, entry_id)
        if entry is None or entry.workspace_id != ws.id:
            return HTMLResponse("not found", status_code=404)
        s.delete(entry)
    return _entries_fragment(request, user_id)


@app.patch("/workspace/entries/{entry_id}", response_class=HTMLResponse)
def patch_workspace_entry(
    request: Request,
    entry_id: int,
    direction: str | None = Form(default=None),
    header_path: str | None = Form(default=None),
    user_id: int = Depends(get_current_user_id),
):
    with session_scope() as s:
        ws = _get_workspace(s, user_id)
        entry = s.get(WorkspaceEntry, entry_id)
        if entry is None or entry.workspace_id != ws.id:
            return HTMLResponse("not found", status_code=404)

        if direction in ("up", "down"):
            if direction == "up":
                neighbor_q = (
                    select(WorkspaceEntry)
                    .where(
                        WorkspaceEntry.workspace_id == ws.id,
                        WorkspaceEntry.position < entry.position,
                    )
                    .order_by(WorkspaceEntry.position.desc())
                    .limit(1)
                )
            else:
                neighbor_q = (
                    select(WorkspaceEntry)
                    .where(
                        WorkspaceEntry.workspace_id == ws.id,
                        WorkspaceEntry.position > entry.position,
                    )
                    .order_by(WorkspaceEntry.position.asc())
                    .limit(1)
                )
            neighbor = s.scalar(neighbor_q)
            if neighbor is not None:
                # UNIQUE(workspace_id, position) requires a sentinel detour.
                # Use a guaranteed-unused negative slot during the swap.
                a, b = entry.position, neighbor.position
                entry.position = -abs(a) - 1
                s.flush()
                neighbor.position = a
                s.flush()
                entry.position = b
                s.flush()

        if header_path is not None:
            entry.header_path = _parse_header_path(header_path) or None

    return _entries_fragment(request, user_id)


@app.get("/workspace/export.docx")
def export_workspace_docx(user_id: int = Depends(get_current_user_id)):
    with session_scope() as s:
        ws = _get_workspace(s, user_id)
        entries = _load_entries(s, ws.id)

        export_entries: list[ExportEntry] = []
        for e in entries:
            path = list(e.header_path or [])
            if e.card is not None:
                export_entries.append(
                    ExportEntry(
                        header_path=path,
                        card=ExportCard(
                            tag=e.card.tag,
                            tag_markup=e.card.tag_markup or [],
                            card_text=e.card.card_text,
                            markup=e.card.markup or [],
                            cite_short=e.card.source.cite_short,
                            raw_cite=e.card.source.raw_cite,
                        ),
                    )
                )
            elif e.analytical is not None:
                export_entries.append(
                    ExportEntry(
                        header_path=path,
                        analytical=ExportAnalytical(
                            argument=e.analytical.argument,
                            argument_markup=e.analytical.argument_markup or [],
                            answer_to=e.analytical.answer_to,
                        ),
                    )
                )

    blob = render_workspace_to_docx("workspace", export_entries)
    return Response(
        content=blob,
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        headers={"Content-Disposition": 'attachment; filename="workspace.docx"'},
    )
