"""Walk Evidence/, ingest each .docx, move successful files to parsed_docs/.

Skips:
- .doc files (legacy format, python-docx can't read them)
- Files matching SKIP_FILENAMES (to-do lists, advocacy statements, source PDFs)
- Files whose source_file name already has cards in the DB (already ingested)
- The garbage/ subdirectory entirely

Per-file flow:
  extract → map → ingest → move to parsed_docs/
"""

from __future__ import annotations

import shutil
import sys
import traceback
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from debatabase.bulk import ingest_docx
from debatabase.db import session_scope
from debatabase.models import Analytical, Card, ContentTag, Source

EVIDENCE_ROOT = Path("Evidence")
PARSED_ROOT = Path("parsed_docs")
EXTRACTED_ROOT = Path("extracted")

SKIP_FILENAMES = {
    "To Do List for Amy.docx",
    "Advocacy Statement.docx",
    # The article-as-docx that was already cut into Cap K cards
    "The Illusion of Resistance Commodification and Reification of Neoliberalism and the State - s10612-017-9374-7.docx",
}

SKIP_DIRS = {"garbage"}


def collect_docx_files(root: Path) -> list[Path]:
    files = []
    for p in sorted(root.rglob("*.docx")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name in SKIP_FILENAMES:
            continue
        files.append(p)
    return files


def already_ingested(filename: str) -> bool:
    with session_scope() as s:
        n = s.execute(
            select(Card.id).where(Card.source_file == filename).limit(1)
        ).scalar_one_or_none()
        if n is not None:
            return True
        n2 = s.execute(
            select(Analytical.id).where(Analytical.source_file == filename).limit(1)
        ).scalar_one_or_none()
        return n2 is not None


def move_to_parsed(src: Path) -> Path:
    """Move docx into parsed_docs/, preserving subdirectory structure relative to Evidence/."""
    rel = src.relative_to(EVIDENCE_ROOT)
    dst = PARSED_ROOT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst


def main() -> None:
    files = collect_docx_files(EVIDENCE_ROOT)
    print(f"Found {len(files)} candidate .docx files in Evidence/\n")

    skipped_already = 0
    skipped_empty = 0
    failed = []
    succeeded = []
    total_cards = 0
    total_analyticals = 0
    all_new_tags: set[str] = set()

    for i, fp in enumerate(files, start=1):
        if already_ingested(fp.name):
            skipped_already += 1
            print(f"  [{i:3d}/{len(files)}] SKIP (already ingested): {fp.name}")
            continue

        rel_display = str(fp.relative_to(EVIDENCE_ROOT))
        print(f"  [{i:3d}/{len(files)}] {rel_display}")
        try:
            jsonl_path = EXTRACTED_ROOT / (fp.stem.replace(" ", "_") + ".jsonl")
            with session_scope() as s:
                stats = ingest_docx(fp, s, save_jsonl_to=jsonl_path)
            ca = stats["cards_added"]
            an = stats["analyticals_added"]
            new_tags = stats["new_tag_slugs"]
            errs = stats["errors"]

            if ca == 0 and an == 0:
                skipped_empty += 1
                print(f"        ⚠ no cards/analyticals extracted — skipping move")
                continue

            total_cards += ca
            total_analyticals += an
            all_new_tags |= set(new_tags)
            succeeded.append((fp, ca, an))
            print(f"        ✓ {ca} cards, {an} analyticals"
                  + (f"  ⚠ {len(errs)} item errors" if errs else ""))
            if errs:
                for e in errs[:3]:
                    print(f"          {e}")
            move_to_parsed(fp)
        except (SQLAlchemyError, Exception) as e:
            failed.append((fp, repr(e)))
            print(f"        ✗ FAILED: {e!r}")
            traceback.print_exc(limit=3, file=sys.stdout)

    # Final summary
    print("\n" + "=" * 70)
    print(f"SUMMARY")
    print(f"  files attempted: {len(files)}")
    print(f"  succeeded: {len(succeeded)}  (cards={total_cards}, analyticals={total_analyticals})")
    print(f"  skipped (already ingested): {skipped_already}")
    print(f"  skipped (no content): {skipped_empty}")
    print(f"  failed: {len(failed)}")
    if failed:
        print(f"\n--- Failures ---")
        for fp, err in failed[:20]:
            print(f"  {fp.name}: {err}")

    with session_scope() as s:
        n_cards = s.execute(select(Card)).scalars().all()
        n_anal = s.execute(select(Analytical)).scalars().all()
        n_src = s.execute(select(Source)).scalars().all()
        n_tags = s.execute(select(ContentTag)).scalars().all()
        print(f"\n--- DB after run ---")
        print(f"  cards={len(n_cards)}  analyticals={len(n_anal)}  "
              f"sources={len(n_src)}  content_tags={len(n_tags)}")


if __name__ == "__main__":
    main()
