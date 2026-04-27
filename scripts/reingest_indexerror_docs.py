"""Delete + re-ingest the 3 docs that had IndexErrors during bulk ingest.

These docs had their last H4 tag with no following paragraph (trailing tag),
which crashed the old map_doc logic. The fix is in bulk.py; this script
re-runs them so the previously-missing last cards get added.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete

from debatabase.bulk import ingest_docx
from debatabase.db import session_scope
from debatabase.models import Analytical, Card

DOCS = [
    Path("parsed_docs/K Aff Stuff/2AC - UDNC Round 1 .docx"),
    Path("parsed_docs/K Aff Stuff/2AC r7(1).docx"),
    Path("parsed_docs/fwk 2AC.docx"),
]


def main() -> None:
    for fp in DOCS:
        if not fp.exists():
            print(f"  ⚠ missing: {fp}")
            continue
        with session_scope() as s:
            n_card = s.execute(delete(Card).where(Card.source_file == fp.name)).rowcount
            n_anal = s.execute(delete(Analytical).where(Analytical.source_file == fp.name)).rowcount
            print(f"  {fp.name}: deleted {n_card} cards + {n_anal} analyticals")
        with session_scope() as s:
            stats = ingest_docx(fp, s)
            errs = stats["errors"]
            print(f"    ✓ re-ingested: {stats['cards_added']} cards, "
                  f"{stats['analyticals_added']} analyticals"
                  + (f"  ⚠ {len(errs)} item errors" if errs else ""))
            for e in errs[:3]:
                print(f"      {e}")


if __name__ == "__main__":
    main()
