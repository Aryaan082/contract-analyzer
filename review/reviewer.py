"""The review loop: from a bundle to checked spans and an honest coverage block (R3).

`run_review` does everything between "here is a bounded bundle" and "here are the `S` records":

1. normalise every file (`review.lexer`), split the definitions into known and new
   (`review.known`) and run the model-free scans (`review.scan`);
2. refuse up front when the new code is over the ceiling: no model is called, the summary says why;
3. cut the new code into calls, send each to the reviewer (no tools, fenced, JSON reply), check each
   reply item by item (`review.spans`), and attach precedents with our own code
   (`review.precedents`);
4. optionally run the blind second pass for findings that ask to be rated high (`review.confirm`);
5. write the coverage block. It is the runner's, never a model's: it counts what was and was not
   read, and a review that did not read everything says `partial` and why.

Nothing here reads the chain or the explorer, and nothing here talks to the writer model. The `S`
records are the only thing that leaves.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from knowledge.findings import FindingsFile
from review.client import UNAVAILABLE, CallRecord, ReviewerClient, call_once
from review.confirm import Confirmation, candidates_of, confirm
from review.credit import default_files
from review.known import (
    CEILING_TOKENS,
    CHUNK_TOKENS,
    Chunk,
    Classified,
    KnownList,
    Split,
    build_outline,
    classify,
    load_known,
    plan_chunks,
    size_check,
)
from review.lexer import LEXER_VERSION, Lexed, lex
from review.pack import Checklist, build_checklist, build_prompt, prompt_sha
from review.precedents import (
    PrecedentMap,
    attach,
    declared_names,
    fork_check,
    load_map,
)
from review.scan import scan_bundle
from review.spans import Accepted, CallContext, capped, to_span
from review.types import (
    Bundle,
    ReviewerInfo,
    ReviewSummary,
    ScanCount,
    SourceSpan,
)

DEFAULT_MAX_TOKENS = 8192  # per call; a model that thinks first bills that thinking as output


@dataclass
class ReviewResult:
    summary: ReviewSummary
    scans: list[SourceSpan] = field(default_factory=list)  # kind "scan": text aimed at a reader...
    spans: list[SourceSpan] = field(default_factory=list)  # kind "span": the reviewer's items
    confirmations: list[SourceSpan] = field(default_factory=list)  # the agreeing second readings
    calls: list[CallRecord] = field(default_factory=list)
    dropped: Counter = field(default_factory=Counter)  # reviewer items thrown away, by reason
    adjusted: Counter = field(default_factory=Counter)  # items kept but corrected, by reason
    lexed: Mapping[str, Lexed] = field(default_factory=dict)  # for the viewer's file payloads
    split: Split | None = None


def run_review(
    bundle: Bundle,
    client: ReviewerClient,
    *,
    known: KnownList | None = None,
    checklist: Checklist | None = None,
    hints: Sequence[str] = (),
    entry: str | None = None,
    ceiling_tokens: int = CEILING_TOKENS,
    max_cost_usd: float | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    chunk_tokens: int = CHUNK_TOKENS,
    second: ReviewerClient | None = None,
    precedent_map: PrecedentMap | None = None,
    findings: Sequence[FindingsFile] | None = None,
) -> ReviewResult:
    """Review `bundle` with `client`. `entry` is the deployed contract's name as the caller knows it
    (it only orders the calls and is never printed). `second` turns on the blind confirmation pass;
    the caller passes it only when the high-risk switch is on, since it costs extra calls."""
    checklist = checklist or build_checklist()
    known = known if known is not None else load_known()
    files = findings if findings is not None else default_files()
    pmap = precedent_map if precedent_map is not None else load_map()

    lexed = {f.file_id: lex(f.file_id, f.content) for f in bundle.files}
    split = classify(bundle.files, lexed, known)
    scans = scan_bundle(bundle, lexed)
    check = size_check(split, ceiling_tokens)
    base = _base_summary(bundle, lexed, split, scans, check.new_tokens, ceiling_tokens)

    def early(summary: ReviewSummary) -> ReviewResult:
        return ReviewResult(summary=summary, scans=scans, lexed=lexed, split=split)

    new = split.with_status("new")
    unread = base["definitions_not_reviewed"] + base["files_not_checked"]
    if not check.ok:
        return early(_summary(base, "refused", check.reason, reviewer_model=client.name))
    if not new:
        return early(_no_model_needed(base, unread, split))

    chunks = plan_chunks(split, lexed, entry=entry, max_tokens=chunk_tokens)
    outline = build_outline(bundle, lexed, split)
    reviewer = ReviewerInfo(
        model=client.name,
        prompt_sha=prompt_sha(),
        checklist_sha=checklist.sha256,
        lexer_version=LEXER_VERSION,
    )
    calls: list[CallRecord] = []
    dropped: Counter = Counter()
    adjusted: Counter = Counter()
    items: list[Accepted] = []
    done: list[bool] = []
    stopped: str | None = None
    for chunk in chunks:
        spent = sum(c.cost_usd for c in calls)
        if max_cost_usd is not None and spent >= max_cost_usd:
            stopped = "the cost cap was reached"
            done.append(False)
            continue
        if stopped == UNAVAILABLE:
            done.append(False)
            continue
        prompt = build_prompt(
            checklist, chunk, outline=outline, hints=hints, total_parts=len(chunks)
        )
        ctx = CallContext.for_chunk(chunk, lexed)
        checked, record = call_once(
            client, prompt, ctx, checklist, purpose="review", max_tokens=max_tokens
        )
        calls.append(record)
        dropped.update(checked.dropped)
        adjusted.update(checked.adjusted)
        done.append(checked.unreadable is None)
        if checked.unreadable == UNAVAILABLE:
            stopped = UNAVAILABLE
        items.extend(checked.accepted)

    confirmation = Confirmation()
    if second is not None and any(done):
        spent = sum(c.cost_usd for c in calls)
        cap = None if max_cost_usd is None else max(max_cost_usd - spent, 0.0)
        confirmation = confirm(
            items,
            candidates_of(items),
            split=split,
            lexed=lexed,
            checklist=checklist,
            second=second,
            first_model=client.name,
            max_tokens=max_tokens,
            spent_cap_usd=cap,
        )
        calls.extend(confirmation.calls)

    spans, confirmations = _make_spans(
        bundle,
        lexed,
        len(scans),
        items,
        confirmation,
        reviewer,
        ReviewerInfo(
            model=second.name if second else client.name,
            prompt_sha=reviewer.prompt_sha,
            checklist_sha=reviewer.checklist_sha,
            lexer_version=LEXER_VERSION,
        ),
        pmap,
        files,
        fork_check(declared_names(lexed), files),
    )

    summary = _final_summary(
        base,
        new,
        _reviewed(new, chunks, done),
        unread,
        done,
        stopped,
        cost=sum(c.cost_usd for c in calls),
        model=client.name,
        confirmation_model=second.name if second and confirmation.agreed else None,
    )
    return ReviewResult(
        summary=summary,
        scans=scans,
        spans=spans,
        confirmations=confirmations,
        calls=calls,
        dropped=dropped,
        adjusted=adjusted,
        lexed=lexed,
        split=split,
    )


# ---- the records --------------------------------------------------------------------------------


def _make_spans(
    bundle: Bundle,
    lexed: Mapping[str, Lexed],
    scan_count: int,
    items: Sequence[Accepted],
    confirmation: Confirmation,
    reviewer: ReviewerInfo,
    second_reviewer: ReviewerInfo,
    pmap: PrecedentMap,
    files: Sequence[FindingsFile],
    forks,
) -> tuple[list[SourceSpan], list[SourceSpan]]:
    first = scan_count + 1
    spans: list[SourceSpan] = []
    confirmations: list[SourceSpan] = []
    agreed = sorted(confirmation.agreed)
    confirm_ids = {i: f"S{first + len(items) + k}" for k, i in enumerate(agreed)}
    for n, item in enumerate(items):
        lines = _stripped_lines(lexed, item)
        precedents = attach(
            item.entry,
            pmap,
            files,
            forks=forks,
            span_text="\n".join(lines),
            function=item.function,
        )
        confirmed = confirm_ids.get(n)
        spans.append(
            to_span(
                item,
                f"S{first + n}",
                bundle,
                reviewer,
                precedents=precedents,
                severity="high" if confirmed else capped(item.severity_claimed),
                confirmed_by=confirmed,
            )
        )
    for n in agreed:
        other = confirmation.agreed[n]
        confirmations.append(
            to_span(
                other,
                confirm_ids[n],
                bundle,
                second_reviewer,
                severity=capped(other.severity_claimed),
            )
        )
    return spans, confirmations


def _stripped_lines(lexed: Mapping[str, Lexed], item: Accepted) -> list[str]:
    view = lexed[item.file_id].view
    lines = view.text.split("\n") if view else []
    return lines[item.start_line - 1 : item.end_line]


# ---- the coverage block -------------------------------------------------------------------------


def _base_summary(
    bundle: Bundle,
    lexed: Mapping[str, Lexed],
    split: Split,
    scans: Sequence[SourceSpan],
    new_tokens: int,
    ceiling: int,
) -> dict:
    flags: Counter = Counter()
    for s in scans:
        if s.scan:
            flags[s.scan.flag] += s.scan.count
    return {
        "files_total": len(bundle.files),
        "chars_total": sum(len(f.content) for f in bundle.files),
        "dropped": bundle.dropped,
        "assembly_blocks": sum(r.assembly_blocks for r in lexed.values()),
        "scan_flags": tuple(ScanCount(flag=f, count=n) for f, n in sorted(flags.items())),
        "definitions_known": len(split.with_status("known")),
        "definitions_new": len(split.with_status("new")),
        "definitions_not_reviewed": len(split.with_status("not_reviewed")),
        "files_not_checked": sum(1 for r in lexed.values() if r.view is None),
        "new_tokens": new_tokens,
        "ceiling_tokens": ceiling,
        "known_libraries": split.known_libraries(),
    }


def _summary(base: dict, status: str, reason: str | None = None, **more) -> ReviewSummary:
    return ReviewSummary(status=status, reason=reason, **{**base, **more})


def _no_model_needed(base: dict, unread: int, split: Split) -> ReviewSummary:
    """Nothing new to read: all known, outline-only or unreadable. No model is called."""
    if not unread:
        return _summary(base, "ran")
    reason = _unread_reason(base)
    covered = base["definitions_known"] > 0
    if covered:
        return _summary(base, "partial", reason)
    return _summary(base, "failed", reason)


def _unread_reason(base: dict) -> str:
    parts = []
    if base["files_not_checked"]:
        parts.append(f"{base['files_not_checked']} file(s) could not be normalised safely")
    if base["definitions_not_reviewed"]:
        parts.append(f"{base['definitions_not_reviewed']} definition(s) could not be parsed")
    return "not read: " + ", ".join(parts)


def _reviewed(
    new: Sequence[Classified], chunks: Sequence[Chunk], done: Sequence[bool]
) -> list[Classified]:
    """New definitions all of whose pieces were in a call that gave a usable reply."""
    out = []
    for item in new:
        d = item.definition
        mine = [
            (chunk, ok)
            for chunk, ok in zip(chunks, done, strict=True)
            if any(
                p.file_id == d.file_id and d.start_line <= p.start_line and p.end_line <= d.end_line
                for p in chunk.pieces
            )
        ]
        if mine and all(ok for _, ok in mine):
            out.append(item)
    return out


def _final_summary(
    base: dict,
    new: Sequence[Classified],
    reviewed: Sequence[Classified],
    unread: int,
    done: Sequence[bool],
    stopped: str | None,
    *,
    cost: float,
    model: str,
    confirmation_model: str | None,
) -> ReviewSummary:
    reasons = []
    failed = sum(1 for ok in done if not ok)
    if stopped == UNAVAILABLE:
        reasons.append(f"{UNAVAILABLE} after {len(done) - failed} of {len(done)} calls")
    elif stopped:
        reasons.append(f"{stopped} after {len(done) - failed} of {len(done)} calls")
    elif failed:
        reasons.append(f"{failed} of {len(done)} reviewer calls gave no usable reply")
    if unread:
        reasons.append(_unread_reason(base))
    file_new: Counter = Counter(i.definition.file_id for i in new)
    file_done: Counter = Counter(i.definition.file_id for i in reviewed)
    files_reviewed = sum(1 for f, n in file_new.items() if file_done[f] == n)
    fields = {
        **base,
        "reviewer_model": model,
        "files_reviewed": files_reviewed,
        "chars_reviewed": sum(i.chars for i in reviewed),
        "definitions_reviewed": len(reviewed),
        "cost_usd": cost,
        "confirmation_model": confirmation_model,
    }
    if reasons:
        status = "partial" if reviewed else "failed"
        return ReviewSummary(status=status, reason="; ".join(reasons), **fields)
    return ReviewSummary(status="ran", **fields)
