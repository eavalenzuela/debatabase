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

import re

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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
from debatabase.markup_ops import apply_op
from debatabase.models import (
    Analytical,
    Card,
    CardContentTag,
    CardVariant,
    ContentTag,
    Source,
    User,
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

        # Current workspace id for "+ Add to workspace" buttons
        current_user = _get_user(s, get_current_user_id())
        current_workspace_id = current_user.current_workspace_id
        if current_workspace_id is None:
            ws_obj = _ensure_current_workspace(s, current_user.id)
            current_workspace_id = ws_obj.id

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
            "current_workspace_id": current_workspace_id,
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
        current_user = _get_user(s, get_current_user_id())
        current_workspace_id = current_user.current_workspace_id
        if current_workspace_id is None:
            ws_obj = _ensure_current_workspace(s, current_user.id)
            current_workspace_id = ws_obj.id

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
            "current_workspace_id": current_workspace_id,
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
# Workspaces
#
# A user can have many named workspaces; one is "current" (where
# "+ Add to workspace" buttons send adds, and where /workspace
# redirects). v1 hardcodes user_id=1 (the bootstrap "local" user);
# FEATURE_ADDITIONS.md #6 replaces get_current_user_id() with a real
# session lookup.
# ---------------------------------------------------------------------------

def get_current_user_id() -> int:
    return 1


def _get_user(s: Session, user_id: int) -> User:
    user = s.get(User, user_id)
    if user is None:
        raise RuntimeError(f"user {user_id} not found")
    return user


def _ensure_current_workspace(s: Session, user_id: int) -> Workspace:
    """Return the user's current workspace, creating one if they have none."""
    user = _get_user(s, user_id)
    if user.current_workspace_id is not None:
        ws = s.get(Workspace, user.current_workspace_id)
        if ws is not None and ws.user_id == user_id:
            return ws
    # Either current is unset or stale; pick any workspace, or make one.
    ws = s.scalar(
        select(Workspace).where(Workspace.user_id == user_id).order_by(Workspace.id)
    )
    if ws is None:
        ws = Workspace(user_id=user_id, name="My Workspace")
        s.add(ws)
        s.flush()
    user.current_workspace_id = ws.id
    return ws


def _get_workspace_or_404(
    s: Session, ws_id: int, user_id: int
) -> Workspace | None:
    ws = s.get(Workspace, ws_id)
    if ws is None or ws.user_id != user_id:
        return None
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
                joinedload(WorkspaceEntry.card_variant),
            )
            .order_by(WorkspaceEntry.position)
        ).scalars().unique().all()
    )


def _entry_markup(entry: WorkspaceEntry) -> list[dict]:
    """Effective markup spans for a card entry: variant if set, else canonical."""
    if entry.card_variant is not None:
        return entry.card_variant.markup or []
    if entry.card is not None:
        return entry.card.markup or []
    return []


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


def _entries_fragment(request: Request, ws: Workspace) -> HTMLResponse:
    with session_scope() as s:
        entries = _load_entries(s, ws.id)
    return templates.TemplateResponse(
        request,
        "_workspace_entries.html",
        {
            "ws": ws,
            "groups": _group_entries(entries),
            "n_entries": len(entries),
        },
    )


def _reorder_to_position(
    s: Session, ws_id: int, entry: WorkspaceEntry, new_position: int
) -> None:
    """Move `entry` to absolute 1-indexed `new_position`, renumbering others.

    Uses a two-pass flush (negative sentinel slots, then flip back to
    positive) to avoid violating UNIQUE(workspace_id, position) mid-update.
    """
    entries = list(
        s.execute(
            select(WorkspaceEntry)
            .where(WorkspaceEntry.workspace_id == ws_id)
            .order_by(WorkspaceEntry.position)
        ).scalars().all()
    )
    others = [e for e in entries if e.id != entry.id]
    new_idx = max(0, min(new_position - 1, len(others)))
    new_order = others[:new_idx] + [entry] + others[new_idx:]

    for i, e in enumerate(new_order, start=1):
        e.position = -i
    s.flush()
    for e in new_order:
        e.position = -e.position
    s.flush()


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _filename_for(name: str) -> str:
    safe = _SAFE_FILENAME.sub("_", name).strip("_") or "workspace"
    return f"{safe}.docx"


# ---- list / create / view / rename / delete / select --------------------

@app.get("/workspaces", response_class=HTMLResponse)
def workspaces_index(
    request: Request, user_id: int = Depends(get_current_user_id)
):
    with session_scope() as s:
        user = _get_user(s, user_id)
        all_ws = s.execute(
            select(
                Workspace,
                func.count(WorkspaceEntry.id).label("n_entries"),
            )
            .outerjoin(
                WorkspaceEntry, WorkspaceEntry.workspace_id == Workspace.id
            )
            .where(Workspace.user_id == user_id)
            .group_by(Workspace.id)
            .order_by(Workspace.id)
        ).all()
        rows = [(ws, n) for ws, n in all_ws]
        current_id = user.current_workspace_id
    return templates.TemplateResponse(
        request,
        "workspaces_index.html",
        {"rows": rows, "current_workspace_id": current_id},
    )


@app.post("/workspaces", response_class=HTMLResponse)
def create_workspace(
    name: str = Form(...), user_id: int = Depends(get_current_user_id)
):
    name = name.strip() or "Untitled"
    with session_scope() as s:
        user = _get_user(s, user_id)
        ws = Workspace(user_id=user_id, name=name)
        s.add(ws)
        s.flush()
        user.current_workspace_id = ws.id
        ws_id = ws.id
    return RedirectResponse(f"/workspaces/{ws_id}", status_code=303)


@app.get("/workspace", response_class=HTMLResponse)
def workspace_redirect(user_id: int = Depends(get_current_user_id)):
    with session_scope() as s:
        ws = _ensure_current_workspace(s, user_id)
        ws_id = ws.id
    return RedirectResponse(f"/workspaces/{ws_id}", status_code=303)


@app.get("/workspaces/{ws_id}", response_class=HTMLResponse)
def workspace_view(
    request: Request,
    ws_id: int,
    user_id: int = Depends(get_current_user_id),
):
    with session_scope() as s:
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse(f"workspace {ws_id} not found", status_code=404)
        user = _get_user(s, user_id)
        entries = _load_entries(s, ws.id)
        is_current = user.current_workspace_id == ws.id
    return templates.TemplateResponse(
        request,
        "workspace.html",
        {
            "ws": ws,
            "groups": _group_entries(entries),
            "n_entries": len(entries),
            "is_current": is_current,
        },
    )


@app.patch("/workspaces/{ws_id}", response_class=HTMLResponse)
def rename_workspace(
    ws_id: int,
    name: str = Form(...),
    user_id: int = Depends(get_current_user_id),
):
    name = name.strip() or "Untitled"
    with session_scope() as s:
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse("not found", status_code=404)
        ws.name = name
    return HTMLResponse(name)


@app.delete("/workspaces/{ws_id}", response_class=HTMLResponse)
def delete_workspace(
    ws_id: int, user_id: int = Depends(get_current_user_id)
):
    with session_scope() as s:
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse("not found", status_code=404)
        user = _get_user(s, user_id)
        s.delete(ws)
        s.flush()
        # Always leave the user with at least one workspace.
        remaining = s.scalar(
            select(Workspace).where(Workspace.user_id == user_id).order_by(Workspace.id)
        )
        if remaining is None:
            remaining = Workspace(user_id=user_id, name="My Workspace")
            s.add(remaining)
            s.flush()
        if user.current_workspace_id == ws_id or user.current_workspace_id is None:
            user.current_workspace_id = remaining.id
    return RedirectResponse("/workspaces", status_code=303)


@app.post("/workspaces/{ws_id}/select", response_class=HTMLResponse)
def select_workspace(
    ws_id: int, user_id: int = Depends(get_current_user_id)
):
    with session_scope() as s:
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse("not found", status_code=404)
        user = _get_user(s, user_id)
        user.current_workspace_id = ws.id
    return RedirectResponse(f"/workspaces/{ws_id}", status_code=303)


# ---- entries ----------------------------------------------------------

@app.post("/workspaces/{ws_id}/entries", response_class=HTMLResponse)
def add_workspace_entry(
    request: Request,
    ws_id: int,
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
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse("workspace not found", status_code=404)
        entry = WorkspaceEntry(
            workspace_id=ws.id,
            position=_next_position(s, ws.id),
            header_path=_parse_header_path(header_path) or None,
            card_id=card_id,
            analytical_id=analytical_id,
        )
        s.add(entry)
        ws_name = ws.name
    return HTMLResponse(
        f'<span class="ws-added">added to <em>{ws_name}</em> · '
        f'<a href="/workspaces/{ws_id}">view</a></span>'
    )


@app.delete(
    "/workspaces/{ws_id}/entries/{entry_id}", response_class=HTMLResponse
)
def delete_workspace_entry(
    request: Request,
    ws_id: int,
    entry_id: int,
    user_id: int = Depends(get_current_user_id),
):
    with session_scope() as s:
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse("workspace not found", status_code=404)
        entry = s.get(WorkspaceEntry, entry_id)
        if entry is None or entry.workspace_id != ws.id:
            return HTMLResponse("entry not found", status_code=404)
        s.delete(entry)
    with session_scope() as s:
        ws = s.get(Workspace, ws_id)
        return _entries_fragment(request, ws)


@app.patch(
    "/workspaces/{ws_id}/entries/{entry_id}", response_class=HTMLResponse
)
def patch_workspace_entry(
    request: Request,
    ws_id: int,
    entry_id: int,
    direction: str | None = Form(default=None),
    position: int | None = Form(default=None),
    header_path: str | None = Form(default=None),
    user_id: int = Depends(get_current_user_id),
):
    with session_scope() as s:
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse("workspace not found", status_code=404)
        entry = s.get(WorkspaceEntry, entry_id)
        if entry is None or entry.workspace_id != ws.id:
            return HTMLResponse("entry not found", status_code=404)

        if position is not None:
            _reorder_to_position(s, ws.id, entry, position)
        elif direction in ("up", "down"):
            cur = entry.position
            target = cur - 1 if direction == "up" else cur + 1
            _reorder_to_position(s, ws.id, entry, target)

        if header_path is not None:
            entry.header_path = _parse_header_path(header_path) or None

    with session_scope() as s:
        ws = s.get(Workspace, ws_id)
        return _entries_fragment(request, ws)


@app.post(
    "/workspaces/{ws_id}/entries/{entry_id}/variant",
    response_class=HTMLResponse,
)
def apply_variant_op(
    request: Request,
    ws_id: int,
    entry_id: int,
    action: str = Form(...),
    start: int = Form(...),
    end: int = Form(...),
    kind: str | None = Form(default=None),
    user_id: int = Depends(get_current_user_id),
):
    """Apply a markup op to the card entry's variant.

    Creates the variant on first edit (initialized from the canonical
    card's markup), then mutates it in place. Variants are
    workspace-scoped; the canonical cards row is never touched.
    """
    if action not in ("add", "clear"):
        return HTMLResponse("invalid action", status_code=400)
    if action == "add" and kind not in ("highlight", "underline"):
        return HTMLResponse("kind required for add", status_code=400)
    if start >= end:
        return HTMLResponse("empty selection", status_code=400)

    with session_scope() as s:
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse("workspace not found", status_code=404)
        entry = s.get(WorkspaceEntry, entry_id)
        if (
            entry is None
            or entry.workspace_id != ws.id
            or entry.card_id is None
        ):
            return HTMLResponse(
                "card entry not found in this workspace", status_code=404
            )

        card = s.get(Card, entry.card_id)
        if card is None:
            return HTMLResponse("card missing", status_code=500)
        if not (0 <= start < end <= len(card.card_text)):
            return HTMLResponse(
                "selection out of bounds", status_code=400
            )

        if entry.card_variant_id is not None:
            variant = s.get(CardVariant, entry.card_variant_id)
        else:
            variant = CardVariant(
                workspace_id=ws.id,
                card_id=card.id,
                markup=list(card.markup or []),
            )
            s.add(variant)
            s.flush()
            entry.card_variant_id = variant.id

        variant.markup = apply_op(
            variant.markup or [],
            action=action,
            kind=kind,  # type: ignore[arg-type]
            start=start,
            end=end,
        )
        variant.updated_at = func.now()

    with session_scope() as s:
        ws = s.get(Workspace, ws_id)
        return _entries_fragment(request, ws)


@app.delete(
    "/workspaces/{ws_id}/entries/{entry_id}/variant",
    response_class=HTMLResponse,
)
def revert_variant(
    request: Request,
    ws_id: int,
    entry_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Drop the variant and revert the entry to the canonical card markup."""
    with session_scope() as s:
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse("workspace not found", status_code=404)
        entry = s.get(WorkspaceEntry, entry_id)
        if entry is None or entry.workspace_id != ws.id:
            return HTMLResponse("entry not found", status_code=404)
        variant_id = entry.card_variant_id
        entry.card_variant_id = None
        if variant_id is not None:
            variant = s.get(CardVariant, variant_id)
            if variant is not None:
                s.delete(variant)
    with session_scope() as s:
        ws = s.get(Workspace, ws_id)
        return _entries_fragment(request, ws)


@app.post("/workspaces/{ws_id}/clear", response_class=HTMLResponse)
def clear_workspace(
    request: Request,
    ws_id: int,
    user_id: int = Depends(get_current_user_id),
):
    with session_scope() as s:
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse("workspace not found", status_code=404)
        s.execute(
            sqltext("DELETE FROM workspace_entries WHERE workspace_id = :wid"),
            {"wid": ws.id},
        )
    with session_scope() as s:
        ws = s.get(Workspace, ws_id)
        return _entries_fragment(request, ws)


@app.get("/workspaces/{ws_id}/export.docx")
def export_workspace_docx(
    ws_id: int, user_id: int = Depends(get_current_user_id)
):
    with session_scope() as s:
        ws = _get_workspace_or_404(s, ws_id, user_id)
        if ws is None:
            return HTMLResponse("workspace not found", status_code=404)
        entries = _load_entries(s, ws.id)
        ws_name = ws.name

        export_entries: list[ExportEntry] = []
        for e in entries:
            path = list(e.header_path or [])
            if e.card is not None:
                # Prefer the workspace-scoped variant's markup when set;
                # variants never touch the canonical card row.
                effective_markup = (
                    e.card_variant.markup
                    if e.card_variant is not None
                    else (e.card.markup or [])
                )
                export_entries.append(
                    ExportEntry(
                        header_path=path,
                        card=ExportCard(
                            tag=e.card.tag,
                            tag_markup=e.card.tag_markup or [],
                            card_text=e.card.card_text,
                            markup=effective_markup or [],
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

    blob = render_workspace_to_docx(ws_name, export_entries)
    return Response(
        content=blob,
        media_type=(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{_filename_for(ws_name)}"'
        },
    )
