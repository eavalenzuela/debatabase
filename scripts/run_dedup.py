"""Full dedup pipeline driver — one command, runs everything.

Pipeline (in order):
  1. Cite canonicalization — merge sources rows that describe the same
     article, so source-grouped dedup can connect more cards.
  2. Tag regeneration (LLM only) — rewrite parser-noise card tags
     (sentinels, all-caps---prefix) so the heuristic can pick them
     correctly in subsequent steps. Skipped without --use-llm.
  3. Embedding-strategy dedup — pgvector cosine clusters, two-stage
     sub-clustering, heuristic decision + optional LLM tiebreaker
     for ambiguous-but-passable margin skips.
  4. Source-strategy dedup — same pipeline but clustered by source_id.
     Catches pairs the embedding scan missed (typos, paragraph context).
  5. Embedding re-pass — picks up markup-superset wins unlocked by
     step 4's state changes.

Each step is idempotent: re-runs are no-ops when the corpus is already
clean. Designed to run as a post-ingest hook (--dedup flag on
bulk_ingest.py and ingest_wiki_dump.py) but also runnable standalone.

Usage:
    uv run python -m scripts.run_dedup            # heuristic-only (fast, free)
    uv run python -m scripts.run_dedup --use-llm  # +tag regen +LLM tiebreaker (~$1-3)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

from debatabase.config import settings


def _has_llm() -> bool:
    return bool(settings.anthropic_api_key)


def run_pipeline(use_llm: bool, report_dir: Path | None = None) -> int:
    """Execute the full dedup pipeline. Returns 0 on success, non-zero on
    fatal error (individual step failures are logged and the pipeline
    continues — idempotent so partial completion is recoverable).
    """
    if use_llm and not _has_llm():
        print("--use-llm requested but ANTHROPIC_API_KEY is not set; "
              "running heuristic-only steps.", file=sys.stderr)
        use_llm = False

    if report_dir is None:
        report_dir = Path(tempfile.mkdtemp(prefix="dedup-auto-"))
    else:
        report_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n=== run_dedup: reports in {report_dir} (use_llm={use_llm}) ===\n")

    # Lazy imports so users not invoking this don't pay the import cost
    from scripts import auto_dedup, canonicalize_sources, regenerate_tags

    # Step 1: cite canonicalization
    print("--- step 1: cite canonicalization ---")
    src_merge = report_dir / "srcmerge.json"
    canonicalize_sources.cmd_dry_run(Namespace(out=str(src_merge), sample=0))
    canonicalize_sources.cmd_apply(Namespace(apply=str(src_merge)))

    # Step 2: tag regeneration (LLM-only)
    if use_llm:
        print("\n--- step 2: tag regeneration (Haiku) ---")
        tagrx = report_dir / "tagrx.json"
        regenerate_tags.cmd_dry_run(Namespace(
            out=str(tagrx), sample=0, limit=None,
        ))
        regenerate_tags.cmd_apply(Namespace(apply=str(tagrx)))
    else:
        print("\n--- step 2: tag regeneration SKIPPED (no LLM) ---")

    # Step 3: embedding-strategy dedup
    print("\n--- step 3: embedding-strategy dedup ---")
    emb = report_dir / "emb.json"
    auto_dedup.cmd_dry_run(Namespace(
        strategy="embedding", out=str(emb), threshold=0.08, sample=0,
    ))
    apply_path = str(emb)
    if use_llm:
        emb_llm = report_dir / "emb-llm.json"
        auto_dedup.cmd_llm_fill(Namespace(
            llm_fill=str(emb), out=str(emb_llm), llm_max=None,
        ))
        apply_path = str(emb_llm)
    auto_dedup.cmd_apply(Namespace(apply=apply_path))

    # Step 4: source-strategy dedup
    print("\n--- step 4: source-strategy dedup ---")
    src = report_dir / "src.json"
    auto_dedup.cmd_dry_run(Namespace(
        strategy="source", out=str(src), threshold=0.08, sample=0,
    ))
    apply_path = str(src)
    if use_llm:
        src_llm = report_dir / "src-llm.json"
        auto_dedup.cmd_llm_fill(Namespace(
            llm_fill=str(src), out=str(src_llm), llm_max=None,
        ))
        apply_path = str(src_llm)
    auto_dedup.cmd_apply(Namespace(apply=apply_path))

    # Step 5: embedding re-pass — markup-superset wins surfaced by step 4
    print("\n--- step 5: embedding re-pass ---")
    emb2 = report_dir / "emb2.json"
    auto_dedup.cmd_dry_run(Namespace(
        strategy="embedding", out=str(emb2), threshold=0.08, sample=0,
    ))
    auto_dedup.cmd_apply(Namespace(apply=str(emb2)))

    print("\n=== run_dedup: done ===\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--use-llm",
        action="store_true",
        help="enable Haiku-using steps (tag regen + LLM tiebreaker). "
             "Costs ~$1-3 per full pass at current corpus size.",
    )
    ap.add_argument(
        "--report-dir",
        type=Path,
        default=None,
        help="where to write per-step JSON reports (defaults to /tmp/dedup-auto-XXXX)",
    )
    args = ap.parse_args(argv)
    return run_pipeline(args.use_llm, args.report_dir)


if __name__ == "__main__":
    sys.exit(main())
