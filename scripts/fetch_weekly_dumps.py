"""Download (and optionally extract) opencaselist weekly bulk dumps.

The dumps are public (no login). Each week's zip lives at:
    https://caselist-files.s3.us-east-005.backblazeb2.com/weekly/{topic}/{topic}-weekly-YYYY-MM-DD.zip

Listing the bucket isn't allowed for unauthenticated requests, so we
HEAD-probe every Tuesday in the requested date range and skip 404s.

Usage:
    uv run python scripts/fetch_weekly_dumps.py
    uv run python scripts/fetch_weekly_dumps.py --start 2026-02-24 --end 2026-04-14
    uv run python scripts/fetch_weekly_dumps.py --topic hspolicy25 --output ~/Downloads/wiki-dumps
    uv run python scripts/fetch_weekly_dumps.py --extract  # also unzip into sibling dirs

Default topic is hspolicy25 and default range is the recent 8 weeks.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

BASE = "https://caselist-files.s3.us-east-005.backblazeb2.com/weekly"


def head_size(url: str) -> int | None:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return int(r.headers.get("Content-Length", 0))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def download(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    tmp.rename(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topic", default="hspolicy25")
    ap.add_argument("--output", type=Path, default=Path.home() / "Downloads/wiki-dumps")
    ap.add_argument("--start", type=date.fromisoformat, default=None)
    ap.add_argument("--end", type=date.fromisoformat, default=None)
    ap.add_argument(
        "--extract",
        action="store_true",
        help="unzip alongside the downloaded zip",
    )
    args = ap.parse_args()

    today = date.today()
    end = args.end or today
    start = args.start or (end - timedelta(weeks=8))
    args.output.mkdir(parents=True, exist_ok=True)

    # Walk every day in range; the dumps are weekly but the day-of-week
    # has shifted historically, so we probe every date.
    available: list[tuple[date, int]] = []
    d = start
    print(f"probing {start} → {end} for available dumps...")
    while d <= end:
        url = f"{BASE}/{args.topic}/{args.topic}-weekly-{d.isoformat()}.zip"
        sz = head_size(url)
        if sz:
            available.append((d, sz))
        d += timedelta(days=1)
    total = sum(sz for _, sz in available)
    print(
        f"found {len(available)} dump(s); {total/1e9:.2f} GB total"
    )

    for d, sz in available:
        url = f"{BASE}/{args.topic}/{args.topic}-weekly-{d.isoformat()}.zip"
        out = args.output / f"{args.topic}-weekly-{d.isoformat()}.zip"
        if out.exists() and out.stat().st_size == sz:
            print(f"  skip {d.isoformat()} ({sz/1e6:.0f} MB, already downloaded)")
        else:
            print(f"  fetch {d.isoformat()} ({sz/1e6:.0f} MB)...")
            t0 = time.monotonic()
            download(url, out)
            print(f"    done in {time.monotonic()-t0:.0f}s")

        if args.extract:
            extract_dir = out.with_suffix("")
            if extract_dir.exists():
                print(f"    extract dir already exists: {extract_dir.name}")
            else:
                print(f"    extracting...")
                with zipfile.ZipFile(out) as zf:
                    zf.extractall(extract_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
