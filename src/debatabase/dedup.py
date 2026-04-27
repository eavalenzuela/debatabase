"""Find clusters of near-duplicate cards using embedding similarity.

A cluster is a transitive group of cards whose pairwise cosine distance
is below ``threshold``. The default 0.08 was chosen empirically: cards
cut from the same article with slightly different highlights typically
land at distance 0.01–0.05; cards on the same topic but different
articles are usually 0.15+. Tune via the ``threshold`` argument if your
corpus differs.

Cards with ``canonical_card_id IS NOT NULL`` are already-resolved
duplicates and are skipped — once a cluster's canonical is chosen, the
cluster doesn't re-surface.

The pair search uses pgvector's HNSW index via a LATERAL join so the
total work is N × K (per-card K-NN) rather than N² pairwise scans. The
union-find pass is pure Python.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text as sqltext
from sqlalchemy.orm import Session


DEFAULT_THRESHOLD = 0.08
NEIGHBORS_PER_CARD = 8


@dataclass
class ClusterMember:
    card_id: int
    tag: str
    cite_short: str
    source_id: int


def _find(parent: dict[int, int], a: int) -> int:
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a


def _union(parent: dict[int, int], a: int, b: int) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[ra] = rb


def find_clusters(
    session: Session,
    threshold: float = DEFAULT_THRESHOLD,
    neighbors_per_card: int = NEIGHBORS_PER_CARD,
) -> list[list[ClusterMember]]:
    """Return clusters of >= 2 near-duplicate cards.

    Each cluster is a list of ``ClusterMember`` ordered by card_id.
    Clusters themselves are ordered by descending size (so the worst
    duplicates surface first in the admin UI).
    """
    pairs = session.execute(
        sqltext("""
            SELECT a.id AS a_id, b.id AS b_id
            FROM cards a
            JOIN LATERAL (
                SELECT cards.id, (a.embedding <=> cards.embedding) AS dist
                FROM cards
                WHERE cards.id != a.id
                  AND cards.embedding IS NOT NULL
                  AND cards.canonical_card_id IS NULL
                ORDER BY a.embedding <=> cards.embedding
                LIMIT :k
            ) b ON b.dist < :threshold
            WHERE a.embedding IS NOT NULL
              AND a.canonical_card_id IS NULL
        """),
        {"k": neighbors_per_card, "threshold": threshold},
    ).all()

    if not pairs:
        return []

    # Union-find over the involved card ids.
    parent: dict[int, int] = {}
    for a_id, b_id in pairs:
        parent.setdefault(a_id, a_id)
        parent.setdefault(b_id, b_id)
        _union(parent, a_id, b_id)

    # Group by representative.
    groups: dict[int, list[int]] = {}
    for cid in parent:
        root = _find(parent, cid)
        groups.setdefault(root, []).append(cid)

    cluster_id_lists = [sorted(ids) for ids in groups.values() if len(ids) >= 2]
    if not cluster_id_lists:
        return []

    # Hydrate (card.tag, source.cite_short, source.id) for display.
    all_ids = [cid for cluster in cluster_id_lists for cid in cluster]
    rows = session.execute(
        sqltext("""
            SELECT c.id, c.tag, c.source_id, s.cite_short
            FROM cards c
            JOIN sources s ON s.id = c.source_id
            WHERE c.id = ANY(:ids)
        """),
        {"ids": all_ids},
    ).all()
    by_id = {
        r.id: ClusterMember(
            card_id=r.id, tag=r.tag, cite_short=r.cite_short, source_id=r.source_id
        )
        for r in rows
    }

    clusters: list[list[ClusterMember]] = [
        [by_id[cid] for cid in cluster if cid in by_id]
        for cluster in cluster_id_lists
    ]
    clusters.sort(key=lambda c: -len(c))
    return clusters
