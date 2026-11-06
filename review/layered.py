"""Layered review: the plain reader first, the checklist pipeline second, the union of both
(Aryaan, 2026-11-06: "plain runs first ... then run the pipeline as a secondary run ... then we take
the union ... so this should be additive only").

Layer 1 is the plain reader (`review.plain`, no checklist). Layer 2 is the existing pipeline
(`review.reviewer.run_review`) run on the same bundle as a separate request. The result is the union
by place (file, contract, function). The union is additive: every place layer 1 flagged is in the
result with layer 1's flags unchanged, whatever layer 2 says or whether it ran at all. A place both
layers raise carries both. (On the 2026-11-05 dev runs the pipeline alone found fewer than plain;
what layer 2 adds is the findings plain misses, 7 of 62 over five runs each.)

This module only runs the two readers and joins their flags. What to do with the places (credit
them, simulate them, rank them) is `verify`. Nothing here reads the answer key.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from review import plain
from review.client import ReviewerClient
from review.known import KnownList
from review.reviewer import ReviewResult, flags_of, run_review
from review.types import Bundle, Flag

LAYERS = ("plain", "pipeline")
Place_key = tuple[str, str, str]  # file path, contract, function ('' when unknown)


@dataclass(frozen=True)
class LayerFlag:
    """What one layer said at a place. `note` is the reader's sentence: untrusted text."""

    layer: str
    severity: str
    start_line: int
    end_line: int
    entry: str  # the checklist entry; 'plain' for the plain reader
    confirmed: bool
    note: str = field(default="", compare=False)


@dataclass(frozen=True)
class Place:
    file: str
    contract: str
    function: str
    flags: tuple[LayerFlag, ...]

    @property
    def key(self) -> Place_key:
        return (self.file, self.contract, self.function)

    @property
    def layers(self) -> frozenset[str]:
        return frozenset(f.layer for f in self.flags)

    @property
    def severity(self) -> str:
        order = {"high": 0, "medium": 1, "low": 2}
        return min((f.severity for f in self.flags), key=lambda s: order.get(s, 3))


@dataclass
class LayeredResult:
    places: list[Place]
    plain_flags: tuple[Flag, ...] = ()
    pipeline_flags: tuple[Flag, ...] = ()
    plain_run: plain.PlainRun | None = None
    pipeline: ReviewResult | None = None
    plain_ok: bool = False  # the plain reader answered every request in a readable form
    pipeline_ok: bool = False
    cost_usd: float = 0.0


def place_key(flag: Flag, paths: Mapping[str, str]) -> Place_key:
    """Plain flags name their file by path, pipeline flags by file id (`F3`): both become a path."""
    return (paths.get(flag.file_id, flag.file_id), flag.contract or "", flag.function or "")


def _layer_flag(layer: str, flag: Flag) -> LayerFlag:
    return LayerFlag(layer, flag.severity, flag.start_line, flag.end_line, flag.entry,
                     flag.confirmed, flag.note)  # fmt: skip


def union_places(
    plain_flags: Sequence[Flag], pipeline_flags: Sequence[Flag], paths: Mapping[str, str]
) -> list[Place]:
    """The union by place. Plain's places come first, in the order the reader wrote them, each with
    its plain flags first; places only the pipeline raised follow. Pure: nothing is dropped, merged
    away or reordered inside a layer."""
    grouped: dict[Place_key, list[LayerFlag]] = {}
    for layer, flags in (("plain", plain_flags), ("pipeline", pipeline_flags)):
        for flag in flags:
            grouped.setdefault(place_key(flag, paths), []).append(_layer_flag(layer, flag))
    return [Place(*key, tuple(flags)) for key, flags in grouped.items()]


def additive_problems(result: LayeredResult, paths: Mapping[str, str]) -> list[str]:
    """Why the result is not additive over layer 1, in words of ours; empty when it is. Every plain
    flag must be in the place it names, unchanged, and no place may have lost one."""
    by_key = {p.key: p for p in result.places}
    problems = []
    for flag in result.plain_flags:
        place = by_key.get(place_key(flag, paths))
        if place is None:
            problems.append("a plain place is missing from the union")
        elif _layer_flag("plain", flag) not in place.flags:
            problems.append("a plain flag was changed or lost in the union")
    seen = sum(1 for p in result.places for f in p.flags if f.layer == "plain")
    if seen != len(result.plain_flags):
        problems.append("the union holds a different number of plain flags than layer 1 made")
    return list(dict.fromkeys(problems))


def run_layered(
    bundle: Bundle,
    plain_client: ReviewerClient,
    pipeline_client: ReviewerClient,
    *,
    known: KnownList | None = None,
    mode: str = "single",
    plain_max_tokens: int = plain.DEFAULT_MAX_TOKENS,
    pipeline_max_tokens: int = 8192,
) -> LayeredResult:
    """Run layer 1, then layer 2 as a separate run, and join them. A layer that cannot answer
    leaves the other layer's places as they are: the second layer can only add."""
    files = [(f.path, f.content) for f in bundle.files]
    paths = {f.file_id: f.path for f in bundle.files}
    first = plain.run_plain(files, plain_client, max_tokens=plain_max_tokens)
    second = run_review(
        bundle, pipeline_client, known=known, second=None, mode=mode,  # type: ignore[arg-type]
        max_tokens=pipeline_max_tokens,
    )  # fmt: skip
    pipeline_flags = flags_of(second)
    result = LayeredResult(
        places=union_places(first.flags, pipeline_flags, paths),
        plain_flags=tuple(first.flags),
        pipeline_flags=tuple(pipeline_flags),
        plain_run=first,
        pipeline=second,
        plain_ok=not first.unavailable and not first.failures,
        pipeline_ok=second.summary.status == "ran",
        cost_usd=first.cost_usd + sum(c.cost_usd for c in second.calls),
    )
    if problems := additive_problems(result, paths):
        raise AssertionError("the layered result is not additive: " + "; ".join(problems))
    return result
