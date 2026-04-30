"""Ingest disclosed cases from an extracted opencaselist weekly dump.

Walks a directory tree of ``{School}/{Team}/{file}.docx``, parses each
filename for school/team/side/tournament/round metadata, then runs the
existing ``bulk.ingest_docx`` pipeline per file with the resulting
wiki_uploads row's id propagated to every card and analytical.

Dedup at the file-content level: each .docx is hashed before ingest and
compared against ``wiki_uploads.file_sha256``. Files we've already seen
just have their ``last_seen`` bumped; their cards aren't re-extracted.
Re-running the script across multiple weekly snapshots is therefore
cheap.

The Claude content tagger is force-disabled here because the wiki
corpus is large enough that per-card Haiku calls would cost real money;
the legacy ``KEYWORD_RULES`` path runs free. After ingest, run
``scripts/retag_cards.py`` if you want Claude-quality tags on the wiki
cards. After ingest, also run ``scripts/backfill_embeddings.py`` to
populate embeddings for the new rows.

Usage:
    uv run python scripts/ingest_wiki_dump.py /path/to/extracted-dump
    uv run python scripts/ingest_wiki_dump.py /path/to/dump --weekly-date 2026-04-14
    uv run python scripts/ingest_wiki_dump.py /path/to/dump --limit 5
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from datetime import date as date_cls
from pathlib import Path

from sqlalchemy import select

from debatabase.bulk import ingest_docx
from debatabase.db import session_scope
from debatabase.models import Topic, WikiUpload
from debatabase.wiki_filename import parse_wiki_filename

WEEKLY_DATE_RE = re.compile(r"hspolicy25-weekly-(\d{4}-\d{2}-\d{2})")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _infer_weekly_date(root: Path) -> date_cls | None:
    """Pull a YYYY-MM-DD out of a path component if one is there."""
    for part in [root.name, *(p.name for p in root.parents)]:
        m = WEEKLY_DATE_RE.search(part)
        if m:
            return date_cls.fromisoformat(m.group(1))
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "root",
        type=Path,
        help="extracted weekly dump directory (e.g. .../hspolicy25)",
    )
    ap.add_argument(
        "--weekly-date",
        type=date_cls.fromisoformat,
        default=None,
        help="snapshot date for first_seen/last_seen tracking; "
        "auto-inferred from the path when omitted",
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="walk the tree and print plans but don't insert",
    )
    ap.add_argument(
        "--topic-slug",
        default=None,
        help=(
            "Topic to stamp ingested cards/uploads with. Defaults to the "
            "topic with is_current=true. Pass an explicit slug when "
            "ingesting a packet for a year that isn't current yet."
        ),
    )
    args = ap.parse_args()

    with session_scope() as s:
        if args.topic_slug:
            t = s.scalar(select(Topic).where(Topic.slug == args.topic_slug))
            if t is None:
                slugs = [r[0] for r in s.execute(select(Topic.slug))]
                print(f"--topic-slug={args.topic_slug!r} not found. Known: {sorted(slugs)}",
                      file=sys.stderr)
                return 2
            topic_id: int | None = t.id
        else:
            t = s.scalar(select(Topic).where(Topic.is_current.is_(True)))
            topic_id = t.id if t else None
    if topic_id is None:
        print("⚠ no topic resolved (no current topic in DB) — rows will have NULL topic_id")
    else:
        print(f"stamping rows with topic_id={topic_id}")

    root: Path = args.root
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    weekly_date = args.weekly_date or _infer_weekly_date(root) or date_cls.today()
    print(f"using weekly_date={weekly_date.isoformat()}")

    docx_files = sorted(root.rglob("*.docx"))
    if args.limit is not None:
        docx_files = docx_files[: args.limit]
    print(f"found {len(docx_files)} .docx file(s)")

    new_uploads = 0
    seen_again = 0
    skipped_unparseable = 0
    cards_added = 0
    analyticals_added = 0
    errors: list[str] = []
    t0 = time.monotonic()

    for i, path in enumerate(docx_files, start=1):
        meta = parse_wiki_filename(path.name)
        if meta is None:
            skipped_unparseable += 1
            continue

        try:
            digest = _sha256(path)
        except Exception as e:
            errors.append(f"{path.name}: hash failed: {e!r}")
            continue

        if args.dry_run:
            print(
                f"  [dry] {path.name}  →  {meta.school}/{meta.team} "
                f"{meta.side} · {meta.tournament} · {meta.round_name}"
            )
            continue

        with session_scope() as s:
            existing = s.scalar(
                select(WikiUpload).where(WikiUpload.file_sha256 == digest)
            )
            if existing is not None:
                if existing.last_seen < weekly_date:
                    existing.last_seen = weekly_date
                seen_again += 1
                continue

            upload = WikiUpload(
                school=meta.school,
                team=meta.team,
                side=meta.side,
                tournament=meta.tournament,
                round_name=meta.round_name,
                source_file=path.name,
                file_sha256=digest,
                first_seen=weekly_date,
                last_seen=weekly_date,
                topic_id=topic_id,
            )
            s.add(upload)
            s.flush()
            upload_id = upload.id

        with session_scope() as s:
            try:
                result = ingest_docx(
                    path,
                    s,
                    wiki_upload_id=upload_id,
                    use_claude_tagger=False,
                    topic_id=topic_id,
                )
            except Exception as e:
                errors.append(f"{path.name}: ingest failed: {e!r}")
                continue
            cards_added += result["cards_added"]
            analyticals_added += result["analyticals_added"]
            for err in result["errors"]:
                errors.append(f"{path.name}: {err}")

        new_uploads += 1
        if i % 25 == 0 or i == len(docx_files):
            elapsed = time.monotonic() - t0
            print(
                f"  [{i:4d}/{len(docx_files)}]  "
                f"new={new_uploads} seen-before={seen_again} "
                f"unparseable={skipped_unparseable}  "
                f"cards={cards_added} analyticals={analyticals_added}  "
                f"({elapsed:.0f}s elapsed)"
            )

    elapsed = time.monotonic() - t0
    print(
        f"\ndone in {elapsed:.0f}s. "
        f"{new_uploads} new uploads, {seen_again} already-seen, "
        f"{skipped_unparseable} unparseable filenames; "
        f"{cards_added} cards + {analyticals_added} analyticals inserted; "
        f"{len(errors)} errors."
    )
    if errors:
        print("\nfirst few errors:")
        for e in errors[:10]:
            print(f"  {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
