from __future__ import annotations

import itertools

from app.domain.models import Feature, Operation

_counter = itertools.count()


def feat(
    start: int,
    end: int,
    *,
    id: str | None = None,
    name: str | None = None,
    kind: str = "misc_feature",
    strand: int = 1,
    truncated: bool = False,
) -> Feature:
    n = next(_counter)
    return Feature(
        id=id or f"f{n}",
        name=name or id or f"feat{n}",
        kind=kind,
        start=start,
        end=end,
        strand=strand,
        truncated=truncated,
    )


def op(kind: str, index: int = 0, reverted: bool = False, **payload) -> Operation:
    return Operation(
        id=f"op{next(_counter)}",
        construct_id="c",
        index=index,
        kind=kind,
        payload=payload,
        reverted=reverted,
    )


def ops(*specs) -> list[Operation]:
    """Build a consecutively-indexed op list from ``(kind, payload)`` pairs."""
    return [op(kind, index=i, **payload) for i, (kind, payload) in enumerate(specs)]


def spans(features: list[Feature]) -> dict[str, tuple[int, int]]:
    return {f.id: (f.start, f.end) for f in features}
