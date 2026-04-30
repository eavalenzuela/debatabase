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
from html import escape as html_escape

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, text as sqltext
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from debatabase.auth import (
    MIN_PASSWORD_LEN,
    hash_password,
    validate_nickname,
    validate_password,
    verify_password,
)
from debatabase.config import settings
from debatabase.db import session_scope
from debatabase.rate_limit import (
    answers_rate_limit,
    check as check_rate_limit,
    login_rate_limit,
    register_rate_limit,
    SEARCH_BURST,
    SEARCH_HOURLY,
)
from debatabase.dedup import find_clusters
from debatabase.answer_finder import (
    generate_inverse_claim,
    has_inverse_capability,
)
from debatabase.embeddings import embed_query, has_embedding_key
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
    Topic,
    User,
    WikiUpload,
    Workspace,
    WorkspaceEntry,
)
from debatabase.web.render import RenderMode, render_card, snippet

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
templates.env.globals["render_card"] = render_card
templates.env.globals["snippet"] = snippet

# Cache-busting suffix for /static URLs. Resolved at process start from
# the bundled style.css mtime — every deploy that touches CSS gets a new
# value, invalidating the Cloudflare and browser caches without needing
# a manual purge. Templates use it as `{{ static_v }}` (see _base.html).
try:
    _static_v = str(int((WEB_DIR / "static" / "style.css").stat().st_mtime))
except OSError:
    _static_v = "0"
templates.env.globals["static_v"] = _static_v

app = FastAPI(title="debatabase")


# ---------------------------------------------------------------------------
# Auth middleware
#
# Pre-#6: get_current_user_id() returned a hardcoded 1. Now it reads the
# signed session cookie. Endpoints that require auth use Depends on the
# required version; pages that show the workspace buttons conditionally
# (search, card detail) read request.session directly.
# ---------------------------------------------------------------------------

# Path prefixes that are visible without logging in. The card corpus
# itself is public; only workspace / variant endpoints require auth.
_PUBLIC_PREFIXES = (
    "/login",
    "/register",
    "/logout",
    "/static/",
    "/search",
    "/tags",
    "/sources/",
    "/cards/",
    "/analyticals",
)
_PUBLIC_EXACT = {"/", "/tags", "/analyticals"}


async def _require_login(request: Request, call_next):
    path = request.url.path
    if (
        path in _PUBLIC_EXACT
        or any(path == p or path.startswith(p) for p in _PUBLIC_PREFIXES)
        or "user_id" in request.session
    ):
        return await call_next(request)
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


# Order matters: the LAST-added middleware is OUTERMOST and runs first.
# We need SessionMiddleware to be outermost so request.session is set up
# by the time _require_login (inner) reads it.
app.add_middleware(BaseHTTPMiddleware, dispatch=_require_login)
# Cookie hardening:
#   - HttpOnly is on by default (Starlette).
#   - SameSite=lax blocks the CSRF cases we care about (cross-site form
#     POST, fetch with credentials) without breaking the top-level
#     navigation we use after login.
#   - https_only is gated on env so local http dev still works; in
#     production the cookie is only sent over TLS.
#   - max_age caps idle session length at ~30 days; absent it, the
#     cookie is a session cookie that ends with the browser, which is
#     fine but inconsistent across users.
_PROD = settings.env.lower() in ("prod", "production")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=_PROD,
    max_age=60 * 60 * 24 * 30,
)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


def get_current_user_id_optional(request: Request) -> int | None:
    return request.session.get("user_id")


def get_current_user_id(request: Request) -> int:
    """Required-auth dep: 401 if no session. The middleware redirects
    GET pages to /login; HTMX/API calls get a clean 401."""
    uid = request.session.get("user_id")
    if uid is None:
        raise HTTPException(status_code=401, detail="login required")
    return uid


def _safe_next(raw: str | None, default: str = "/workspaces") -> str:
    """Reject open-redirect targets in the ``next`` param.

    Only allow same-origin, single-segment paths starting with ``/``.
    Strip protocol-relative ``//evil.com`` and absolute URLs.
    """
    if not raw:
        return default
    if not raw.startswith("/"):
        return default
    # Protocol-relative and back-slash variants both get parsed as
    # cross-origin by some browsers; reject both forms outright.
    if raw.startswith("//") or raw.startswith("/\\"):
        return default
    return raw


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
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = Query(default="/workspaces")):
    safe = _safe_next(next)
    if "user_id" in request.session:
        return RedirectResponse(safe, status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"next": safe, "error": None}
    )


@app.post("/login", response_class=HTMLResponse, dependencies=[Depends(login_rate_limit)])
def login_submit(
    request: Request,
    nickname: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/workspaces"),
):
    safe = _safe_next(next)
    with session_scope() as s:
        user = s.scalar(select(User).where(User.nickname == nickname.strip()))
        if user is None or not verify_password(user.pw_hash, password):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"next": safe, "error": "invalid nickname or password"},
                status_code=401,
            )
        user_id = user.id
        user_nick = user.nickname
    request.session["user_id"] = user_id
    request.session["user_nick"] = user_nick
    return RedirectResponse(safe, status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if "user_id" in request.session:
        return RedirectResponse("/workspaces", status_code=303)
    with session_scope() as s:
        # Offer the "claim local" option only if it would actually do
        # anything: no real users yet AND a placeholder user exists.
        any_real_user = s.scalar(
            select(func.count(User.id)).where(User.pw_hash.is_not(None))
        )
        placeholder = s.scalar(
            select(User).where(User.pw_hash.is_(None)).order_by(User.id).limit(1)
        )
        offer_claim = (any_real_user == 0) and (placeholder is not None)
    return templates.TemplateResponse(
        request,
        "register.html",
        {"error": None, "offer_claim": offer_claim, "min_password_len": MIN_PASSWORD_LEN},
    )


@app.post("/register", response_class=HTMLResponse, dependencies=[Depends(register_rate_limit)])
def register_submit(
    request: Request,
    nickname: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    claim_local: bool = Form(default=False),
):
    nick_err = validate_nickname(nickname)
    if nick_err:
        return _register_error(request, nick_err)
    pw_err = validate_password(password)
    if pw_err:
        return _register_error(request, pw_err)
    if password != password_confirm:
        return _register_error(request, "passwords don't match")

    nickname = nickname.strip()
    with session_scope() as s:
        clash = s.scalar(select(User).where(User.nickname == nickname))
        if clash is not None:
            return _register_error(request, "nickname already taken")

        user_id: int
        if claim_local:
            any_real = s.scalar(
                select(func.count(User.id)).where(User.pw_hash.is_not(None))
            )
            placeholder = (
                s.scalar(
                    select(User)
                    .where(User.pw_hash.is_(None))
                    .order_by(User.id)
                    .limit(1)
                )
                if any_real == 0
                else None
            )
            if placeholder is not None:
                placeholder.nickname = nickname
                placeholder.pw_hash = hash_password(password)
                user_id = placeholder.id
            else:
                user_id = _create_fresh_user(s, nickname, password)
        else:
            user_id = _create_fresh_user(s, nickname, password)

    request.session["user_id"] = user_id
    request.session["user_nick"] = nickname
    return RedirectResponse("/workspaces", status_code=303)


def _create_fresh_user(s: Session, nickname: str, password: str) -> int:
    user = User(nickname=nickname, pw_hash=hash_password(password))
    s.add(user)
    s.flush()
    ws = Workspace(user_id=user.id, name="My Workspace")
    s.add(ws)
    s.flush()
    user.current_workspace_id = ws.id
    return user.id


def _register_error(request: Request, error: str) -> HTMLResponse:
    with session_scope() as s:
        any_real_user = s.scalar(
            select(func.count(User.id)).where(User.pw_hash.is_not(None))
        )
        placeholder = s.scalar(
            select(User).where(User.pw_hash.is_(None)).order_by(User.id).limit(1)
        )
        offer_claim = (any_real_user == 0) and (placeholder is not None)
    return templates.TemplateResponse(
        request,
        "register.html",
        {
            "error": error,
            "offer_claim": offer_claim,
            "min_password_len": MIN_PASSWORD_LEN,
        },
        status_code=400,
    )


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    return _search(request, q=None, tag=None, topic=None, page=1)


@app.get("/search", response_class=HTMLResponse)
def search(
    request: Request,
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    # Only charge the rate-limit when there's a query string to embed —
    # tag-only browsing and the empty home view don't call Voyage.
    if q:
        check_rate_limit(
            request, SEARCH_BURST, SEARCH_HOURLY, prefix="search"
        )
    return _search(request, q=q, tag=tag, topic=topic, page=page)


def _vector_literal(vec: list[float]) -> str:
    """pgvector accepts vectors as the textual form '[v1,v2,...]'.

    Used inline in raw SQL where binding a vector via SQLAlchemy's
    sqltext() doesn't pick up pgvector's adapter.
    """
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _embed_query_safe(q: str) -> list[float] | None:
    """Embed `q` if a key is set; swallow API failures so search still works."""
    if not q or not has_embedding_key():
        return None
    try:
        return embed_query(q)
    except Exception:
        return None


def _resolve_topic(s: Session, topic_param: str | None) -> Topic | None:
    """Map ``?topic=`` to a Topic row, or None for "all topics".

    - ``None`` (param absent) → the current topic (is_current=true), if any.
    - ``"all"`` → None (explicit opt-out — search every topic).
    - A slug → that Topic, or None if it doesn't exist (treated as "all").
    """
    if topic_param == "all":
        return None
    if topic_param:
        return s.scalar(select(Topic).where(Topic.slug == topic_param))
    return s.scalar(select(Topic).where(Topic.is_current.is_(True)))


def _search(
    request: Request,
    q: str | None,
    tag: str | None,
    topic: str | None,
    page: int,
):
    offset = (page - 1) * PAGE_SIZE
    qvec = _embed_query_safe(q) if q else None
    semantic_active = qvec is not None
    with session_scope() as s:
        # Resolve the topic filter once. `active_topic` is the row used to
        # filter results AND to render the chip / tooltip. `topic_param`
        # is what we round-trip through pagination/tag links so the user's
        # explicit "all" choice survives navigation.
        active_topic = _resolve_topic(s, topic)
        topic_param = topic if topic else (
            active_topic.slug if active_topic is not None else "all"
        )
        all_topics = s.execute(
            select(Topic).order_by(Topic.season_start.desc(), Topic.id.desc())
        ).scalars().all()

        # Base card-fetch query
        params: dict = {}

        # Build WHERE clauses for cards. Non-canonical (duplicate)
        # cards never surface in search — see /admin/duplicates.
        where_clauses = [sqltext("cards.canonical_card_id IS NULL")]
        if active_topic is not None:
            where_clauses.append(Card.topic_id == active_topic.id)
        if q:
            # tsvector match OR substring match against author_last/cite_short.
            # When semantic is on, also include cards whose embedding is
            # close enough to the query (cosine distance < 0.5).
            if semantic_active:
                where_clauses.append(sqltext("""(
                  cards.search_tsv @@ websearch_to_tsquery('english', :q)
                  OR sources.author_last ILIKE :q_like
                  OR sources.cite_short ILIKE :q_like
                  OR (cards.embedding IS NOT NULL
                      AND (cards.embedding <=> CAST(:qvec AS vector)) < 0.5)
                )"""))
                params["qvec"] = _vector_literal(qvec)
            else:
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
            .options(
                joinedload(Card.source),
                joinedload(Card.wiki_upload),
            )
        )
        # Card count query — same WHERE, no JOIN needed if we wrap
        count_stmt = (
            select(func.count(Card.id))
            .join(Source, Source.id == Card.source_id)
        )
        for clause in where_clauses:
            card_stmt = card_stmt.where(clause)
            count_stmt = count_stmt.where(clause)

        # Order: with semantic on, blend tsvector rank with cosine
        # similarity; cards without embeddings use ts_rank only.
        if q and semantic_active:
            card_stmt = card_stmt.order_by(sqltext("""
                CASE
                  WHEN cards.embedding IS NOT NULL THEN
                    COALESCE(ts_rank(cards.search_tsv, websearch_to_tsquery('english', :q)), 0) * 0.5
                    + (1 - (cards.embedding <=> CAST(:qvec AS vector))) * 0.5
                  ELSE
                    COALESCE(ts_rank(cards.search_tsv, websearch_to_tsquery('english', :q)), 0)
                END DESC, cards.id DESC
            """))
        elif q:
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
            if active_topic is not None:
                anal_stmt = anal_stmt.where(Analytical.topic_id == active_topic.id)
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

        # Counts for header. Cards/analyticals are scoped to the active
        # topic so the header reflects what the user is actually browsing;
        # sources and tags are cross-topic vocabulary and stay global.
        n_cards_stmt = select(func.count(Card.id)).where(
            Card.canonical_card_id.is_(None)
        )
        n_anal_stmt = select(func.count(Analytical.id))
        if active_topic is not None:
            n_cards_stmt = n_cards_stmt.where(Card.topic_id == active_topic.id)
            n_anal_stmt = n_anal_stmt.where(Analytical.topic_id == active_topic.id)
        n_cards = s.execute(n_cards_stmt).scalar_one()
        n_anal = s.execute(n_anal_stmt).scalar_one()
        n_sources = s.execute(select(func.count(Source.id))).scalar_one()
        n_tags = s.execute(select(func.count(ContentTag.id))).scalar_one()

        # Tag tree for sidebar
        tag_tree = _tag_tree(s)

        # Current workspace id (None if not logged in) — controls whether
        # the "+ Add to workspace" buttons render.
        current_user_id = request.session.get("user_id")
        current_workspace_id = None
        current_user_nick = None
        if current_user_id is not None:
            current_user = s.get(User, current_user_id)
            if current_user is not None:
                current_user_nick = current_user.nickname
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
            "topic": topic_param,
            "active_topic": active_topic,
            "all_topics": all_topics,
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
            "current_user_nick": current_user_nick,
            "semantic_active": semantic_active,
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
        card = s.get(
            Card, card_id,
            options=[joinedload(Card.source), joinedload(Card.wiki_upload)],
        )
        if card is None:
            return HTMLResponse(f"Card {card_id} not found", status_code=404)
        tag_rows = s.execute(
            select(ContentTag.slug, ContentTag.label, CardContentTag.status)
            .join(CardContentTag, CardContentTag.content_tag_id == ContentTag.id)
            .where(CardContentTag.card_id == card_id)
        ).all()

        # Pedagogical sidebar: when this card IS canonical, show every
        # alternative cutting (cards whose canonical_card_id points here).
        # When this card IS a duplicate, show a banner pointing at its
        # canonical so direct-link users know the "main" version.
        alt_cuts: list[Card] = []
        canonical_of: Card | None = None
        if card.canonical_card_id is None:
            alt_cuts = list(s.execute(
                select(Card)
                .where(Card.canonical_card_id == card.id)
                .order_by(Card.id.desc())
                .options(
                    joinedload(Card.source),
                    joinedload(Card.wiki_upload),
                )
            ).scalars().unique().all())
        else:
            canonical_of = s.get(
                Card, card.canonical_card_id,
                options=[joinedload(Card.source)],
            )

        current_user_id = request.session.get("user_id")
        current_workspace_id = None
        if current_user_id is not None:
            cu = s.get(User, current_user_id)
            if cu is not None:
                current_workspace_id = cu.current_workspace_id
                if current_workspace_id is None:
                    ws_obj = _ensure_current_workspace(s, cu.id)
                    current_workspace_id = ws_obj.id

    has_highlight = any(s["kind"] == "highlight" for s in (card.markup or []))
    has_underline = any(s["kind"] == "underline" for s in (card.markup or []))
    can_find_answers = has_inverse_capability() and has_embedding_key()

    def _markup_counts(spans: list[dict]) -> tuple[int, int]:
        h = sum(1 for s in (spans or []) if s.get("kind") == "highlight")
        u = sum(1 for s in (spans or []) if s.get("kind") == "underline")
        return h, u

    alt_cuts_view = [
        {
            "card": alt,
            "highlight_count": _markup_counts(alt.markup)[0],
            "underline_count": _markup_counts(alt.markup)[1],
        }
        for alt in alt_cuts
    ]

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
            "can_find_answers": can_find_answers,
            "alt_cuts": alt_cuts_view,
            "canonical_of": canonical_of,
        },
    )


# ---------------------------------------------------------------------------
# Answer-finder: given a card, find evidence that says the inverse
# ---------------------------------------------------------------------------

ANSWERS_LIMIT = 8


@app.get(
    "/cards/{card_id}/answers",
    response_class=HTMLResponse,
    dependencies=[Depends(answers_rate_limit)],
)
def card_answers(request: Request, card_id: int):
    """Generate the inverse claim of `card_id`'s tag, then vector-search."""
    if not has_inverse_capability() or not has_embedding_key():
        return HTMLResponse(
            '<p class="answers-empty">Set ANTHROPIC_API_KEY and '
            'VOYAGE_API_KEY in .env to enable this.</p>',
            status_code=503,
        )

    with session_scope() as s:
        card = s.get(Card, card_id, options=[joinedload(Card.source)])
        if card is None:
            return HTMLResponse("card not found", status_code=404)
        tag = card.tag

    try:
        inverse = generate_inverse_claim(tag)
    except Exception as e:
        return HTMLResponse(
            f'<p class="answers-error">inverse-claim generation failed: '
            f'{html_escape(type(e).__name__)}</p>',
            status_code=502,
        )
    try:
        qvec = embed_query(inverse)
    except Exception as e:
        return HTMLResponse(
            f'<p class="answers-error">embedding failed: '
            f'{html_escape(type(e).__name__)}</p>',
            status_code=502,
        )

    with session_scope() as s:
        rows = s.execute(
            sqltext("""
                SELECT cards.id, cards.tag, cards.card_text, cards.source_id,
                       sources.cite_short, sources.publication,
                       (cards.embedding <=> CAST(:qvec AS vector)) AS distance
                FROM cards
                JOIN sources ON sources.id = cards.source_id
                WHERE cards.embedding IS NOT NULL
                  AND cards.id != :exclude_id
                  AND cards.canonical_card_id IS NULL
                ORDER BY cards.embedding <=> CAST(:qvec AS vector) ASC
                LIMIT :limit
            """),
            {
                "qvec": _vector_literal(qvec),
                "exclude_id": card_id,
                "limit": ANSWERS_LIMIT,
            },
        ).all()

    return templates.TemplateResponse(
        request,
        "_card_answers.html",
        {"inverse": inverse, "rows": rows, "source_card_id": card_id},
    )


# ---------------------------------------------------------------------------
# Admin: proposed-tag review (PR 7a)
#
# Claude-driven tagging (#3 / tagger.py) inserts content_tag links with
# status='proposed'. This page lets the user audit those proposals,
# grouped by content_tag (the auditable axis — "is Claude over-proposing
# co-optation?"). One-click promote to 'approved' or delete entirely.
#
# Login-gated only — no separate admin role yet; this is a personal tool.
# ---------------------------------------------------------------------------

@app.get("/admin/proposed-tags", response_class=HTMLResponse)
def proposed_tags_index(
    request: Request, _user_id: int = Depends(get_current_user_id)
):
    with session_scope() as s:
        rows = s.execute(
            sqltext("""
                SELECT ct.id AS tag_id, ct.slug, ct.label,
                       c.id AS card_id, c.tag AS card_tag, c.card_text,
                       sources.cite_short
                FROM card_content_tags cct
                JOIN content_tags ct ON ct.id = cct.content_tag_id
                JOIN cards c ON c.id = cct.card_id
                JOIN sources ON sources.id = c.source_id
                WHERE cct.status = 'proposed'
                ORDER BY ct.slug, c.id
            """)
        ).all()
    # Group by tag
    groups: list[dict] = []
    current_tag_id: int | None = None
    for r in rows:
        if r.tag_id != current_tag_id:
            groups.append(
                {
                    "tag_id": r.tag_id,
                    "slug": r.slug,
                    "label": r.label,
                    "cards": [],
                }
            )
            current_tag_id = r.tag_id
        groups[-1]["cards"].append(
            {
                "card_id": r.card_id,
                "card_tag": r.card_tag,
                "card_snippet": (r.card_text or "")[:200],
                "cite_short": r.cite_short,
            }
        )
    return templates.TemplateResponse(
        request,
        "proposed_tags.html",
        {"groups": groups, "total": len(rows)},
    )


@app.post(
    "/admin/proposed-tags/{card_id}/{tag_id}/approve",
    response_class=HTMLResponse,
)
def approve_proposed_tag(
    card_id: int, tag_id: int, _user_id: int = Depends(get_current_user_id)
):
    with session_scope() as s:
        s.execute(
            sqltext("""
                UPDATE card_content_tags
                SET status = 'approved'
                WHERE card_id = :cid AND content_tag_id = :tid
                  AND status = 'proposed'
            """),
            {"cid": card_id, "tid": tag_id},
        )
    return HTMLResponse('<span class="proposed-action-ok">approved</span>')


@app.delete(
    "/admin/proposed-tags/{card_id}/{tag_id}", response_class=HTMLResponse
)
def reject_proposed_tag(
    card_id: int, tag_id: int, _user_id: int = Depends(get_current_user_id)
):
    with session_scope() as s:
        s.execute(
            sqltext("""
                DELETE FROM card_content_tags
                WHERE card_id = :cid AND content_tag_id = :tid
                  AND status = 'proposed'
            """),
            {"cid": card_id, "tid": tag_id},
        )
    return HTMLResponse('<span class="proposed-action-ok">rejected</span>')


# ---------------------------------------------------------------------------
# Admin: duplicate-card review (PR 7b, FEATURE_ADDITIONS.md #4)
#
# Surfaces clusters of near-duplicate cards detected via embedding
# cosine distance. The user picks a canonical per cluster; the others
# get cards.canonical_card_id pointed at it and are filtered out of
# /search and /cards/{id}/answers (canonical_card_id IS NULL).
# ---------------------------------------------------------------------------

DUPLICATES_PAGE_SIZE = 25


@app.get("/admin/duplicates", response_class=HTMLResponse)
def duplicates_index(
    request: Request,
    threshold: float = Query(default=0.08, ge=0.0, le=0.5),
    page: int = Query(default=1, ge=1),
    _user_id: int = Depends(get_current_user_id),
):
    with session_scope() as s:
        all_clusters = find_clusters(s, threshold=threshold)
    total_clusters = len(all_clusters)
    total_pages = max(1, (total_clusters + DUPLICATES_PAGE_SIZE - 1) // DUPLICATES_PAGE_SIZE)
    page = min(page, total_pages)
    offset = (page - 1) * DUPLICATES_PAGE_SIZE
    clusters = all_clusters[offset : offset + DUPLICATES_PAGE_SIZE]
    return templates.TemplateResponse(
        request,
        "duplicates.html",
        {
            "clusters": clusters,
            "threshold": threshold,
            "page": page,
            "total_pages": total_pages,
            "total_clusters": total_clusters,
            "start_idx": offset + 1 if clusters else 0,
            "end_idx": offset + len(clusters),
        },
    )


@app.post("/admin/duplicates/canonical", response_class=HTMLResponse)
def set_canonical(
    request: Request,
    canonical_id: int = Form(...),
    # Per-row hidden inputs each named "member_ids" — FastAPI/Starlette
    # collect repeated names into a list. Cards whose <li> got removed
    # by a prior split-off click naturally drop out of this list, so the
    # server never re-marks them.
    member_ids: list[int] = Form(...),
    _user_id: int = Depends(get_current_user_id),
):
    """Mark every member except `canonical_id` as a duplicate of it.

    Returns HTML that REPLACES the entire .dup-cluster (HTMX
    hx-swap=outerHTML) so the cluster visibly disappears once resolved
    — this is what the old "swap inner status only" UI was missing.
    """
    if canonical_id not in member_ids:
        return HTMLResponse(
            "canonical_id must be one of member_ids", status_code=400
        )
    others = [i for i in member_ids if i != canonical_id]
    with session_scope() as s:
        if others:
            s.execute(
                sqltext("""
                    UPDATE cards
                    SET canonical_card_id = :canon
                    WHERE id = ANY(:others)
                """),
                {"canon": canonical_id, "others": others},
            )
        # Ensure the canonical itself is NOT marked as a duplicate.
        s.execute(
            sqltext(
                "UPDATE cards SET canonical_card_id = NULL WHERE id = :id"
            ),
            {"id": canonical_id},
        )
    return HTMLResponse(
        f'<div class="dup-resolved">resolved — canonical = '
        f'<a href="/cards/{canonical_id}">#{canonical_id}</a> '
        f'({len(others)} hidden)</div>'
    )


@app.post("/admin/duplicates/exclude", response_class=HTMLResponse)
def exclude_from_dedup(
    request: Request,
    card_id: int = Form(...),
    _user_id: int = Depends(get_current_user_id),
):
    """Mark a card as "not a duplicate of anything" — it stays visible
    in /search but never re-appears in a duplicate cluster.

    Returns an empty body so HTMX hx-swap=outerHTML on the row removes
    the <li> in place. The form's hidden member_ids input lives inside
    the <li>, so the cluster's submission shrinks naturally.
    """
    with session_scope() as s:
        s.execute(
            sqltext(
                "UPDATE cards SET dedup_excluded = true WHERE id = :id"
            ),
            {"id": card_id},
        )
    return HTMLResponse("")


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
# redirects).
# ---------------------------------------------------------------------------

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
    return HTMLResponse(html_escape(name))


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
        f'<span class="ws-added">added to <em>{html_escape(ws_name)}</em> · '
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
