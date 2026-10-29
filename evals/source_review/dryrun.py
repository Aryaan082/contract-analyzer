"""The free dry run: every part of the kit end to end, with the scripted reviewer.

    uv run python -m evals.source_review dry-run

What it does, in order, and none of it calls a model, an explorer or the network:

  1. loads the cards, checks them and the written split, and counts the labels nobody has reviewed;
  2. runs the hostile suite (gate 1);
  3. reviews every planted variant (vulnerable code, safe twin, renamed copy) with a reviewer that
     reports exactly the labelled functions, and scores the runs: precision, recall, taxonomy
     accuracy, twin discrimination, the renamed-copy gap, honesty, credit correctness;
  4. runs the hostile pairs twice and scores the injection flip rate;
  5. prints the T1, T2, T2b and T3 cost estimates.

THE NUMBERS IT PRINTS TEST THE HARNESS. A reviewer that is handed the answer scores perfectly, and
that proves only that the scorer, the runner and the cards agree with each other. They say nothing
about any model. Contracts on a chain (incident, benign, grey cards) are not run: their source is
fetched at run time, which needs the explorer key, and the paid stages need Aryaan's yes.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from evals.source_review import costs, split
from evals.source_review.cards import ORIGINS, CardSet, load_cards, problems
from evals.source_review.hostile import run_scenario, run_suite
from evals.source_review.hostile_cases import cases
from evals.source_review.items import scenario_for_card
from evals.source_review.scoring import (
    Truth,
    credit_report,
    false_alarms,
    function_scores,
    honesty,
    injection_flips,
    memorisation_gap,
    reached_high,
    stripping_cost,
    twin_discrimination,
)
from evals.source_review.t1 import pair_cases, t1_plan

REPEATS = 2
BANNER = (
    "SCRIPTED REVIEWER: these numbers test the harness (it is handed the labels). They say "
    "nothing about any model, and no experiment has run: evals/results.md gets no row."
)


@dataclass
class DryRun:
    lines: list[str] = field(default_factory=list)
    ok: bool = True
    data: dict = field(default_factory=dict)

    def say(self, text: str = "") -> None:
        self.lines.append(text)

    def fail(self, text: str) -> None:
        self.ok = False
        self.say(f"  PROBLEM: {text}")


def _cards_section(run: DryRun, cards: CardSet) -> None:
    run.say("1. Cards and split")
    counts = cards.counts()
    run.say(
        "   cards: " + ", ".join(f"{o} {counts.get(o, 0)}" for o in ORIGINS) + f" = {len(cards)}"
    )
    sides = {s: sum(1 for c in cards if c.split == s) for s in ("dev", "held_out")}
    run.say(
        f"   split: dev {sides['dev']}, held_out {sides['held_out']} (rule and file: split.toml)"
    )
    unreviewed, drafted = cards.unreviewed_labels()
    run.say(
        f"   {unreviewed} of {drafted} drafted labels unreviewed; "
        f"{drafted} of {len(cards)} cards have a drafted label"
    )
    contract_cards = [c for c in cards if c.contract is not None]
    contract_drafted = [c for c in contract_cards if c.labelled]
    run.say(
        f"   contract cards (fetched at run time, not run here): {len(contract_cards)}, "
        f"{len(contract_drafted)} with a drafted label, "
        f"{len(contract_cards) - len(contract_drafted)} unlabelled"
    )
    for problem in [*problems(cards), *split.check(cards)]:
        run.fail(problem)
    run.data["cards"] = {"counts": counts, "unreviewed": unreviewed, "drafted": drafted}


def _hostile_section(run: DryRun) -> None:
    run.say("2. Hostile suite, gate 1 (scripted, deterministic)")
    result = run_suite(cases())
    for line in result.lines():
        run.say(line)
    if not result.passed:
        run.ok = False
    run.say(f"   {sum(c.passed for c in result.cases)} of {len(result.cases)} cases pass")
    run.data["hostile"] = {"cases": len(result.cases), "passed": result.passed}


def _planted_section(run: DryRun, cards: CardSet) -> None:
    run.say("3. Planted variants, scored (the reviewer is handed the labels)")
    runs: dict[str, list] = {}
    truths: dict[str, Truth] = {}
    for card in cards.of("planted"):
        scenario = scenario_for_card(card, flag_labels=bool(card.functions))
        observed = [run_scenario(scenario) for _ in range(REPEATS)]
        runs[card.id] = [o.result for o in observed]
        truths[card.id] = Truth.of(observed[0].bundle)
    scores = function_scores(cards, runs, origins=("planted",))
    run.say(
        f"   {scores.items} labelled items, {scores.labelled_functions} labelled functions, "
        f"R={REPEATS}: precision {scores.precision}, recall {scores.recall}, "
        f"taxonomy accuracy {scores.taxonomy_accuracy}"
    )
    twins = twin_discrimination(cards, runs)
    run.say(
        f"   twin discrimination: {twins.separated} of {twins.pairs} pairs flagged in the "
        "vulnerable code and not in the safe twin"
    )
    gap = memorisation_gap(cards, runs)
    if gap is not None:
        run.say(f"   renamed-copy gap (original minus renamed recall): {gap.interval}")
    safe_ids = [c.id for c in cards.of("planted") if c.id in cards.twin_ids]
    alarms = false_alarms(cards, runs, origins=("planted",), ids=safe_ids)
    run.say(
        f"   false alarms on the safe twins (stand-ins for benign code): "
        f"{alarms.with_flag} of runs, {len(alarms.flagged_contracts)} contracts flagged"
    )
    high = reached_high(cards, runs, origins=("planted",), ids=safe_ids)
    run.say(f"   safe twins that reach high: {high.count} of {high.contracts}")
    honest = honesty(runs, truths)
    run.say(f"   honesty (coverage block matches the bundle): {honest.honest}")
    flat = [r for results in runs.values() for r in results]
    credit = credit_report(flat)
    run.say(
        f"   credit: {credit.spans} spans, {credit.with_precedent} with a precedent "
        f"(close {credit.closeness['close']}, related {credit.closeness['related']}, "
        f"none {credit.closeness['none']}); unresolved {len(credit.unresolved)}, "
        f"bad URLs {len(credit.bad_urls)}, forbidden phrases {len(credit.forbidden)}"
    )
    if not credit.ok:
        run.fail("a credit check failed")
    cost = stripping_cost(cards, runs, runs, origins=("planted",))
    if cost is not None:
        run.say(
            f"   stripping cost, plumbing only (the raw-view arm does not exist yet; the same runs "
            f"are fed twice): {cost.difference.interval} over {cost.items} items"
        )
    if scores.recall.mean != 1.0 or scores.precision.mean != 1.0 or twins.separated != twins.pairs:
        run.fail("a reviewer that is handed the labels should score perfectly")
    run.data["planted"] = {"items": scores.items, "recall": scores.recall.mean}


def _flips_section(run: DryRun) -> None:
    run.say("4. Injection flip rate over repeats (hostile pairs, scripted)")
    pairs = {}
    for case in pair_cases():
        hostile = [run_scenario(case.hostile).result for _ in range(REPEATS)]
        twin = [run_scenario(case.clean).result for _ in range(REPEATS)]
        pairs[case.id] = (hostile, twin)
    flips = injection_flips(pairs)
    run.say(
        f"   {flips.cases} pairs, {flips.runs} hostile runs: flip rate {flips.rate}, "
        f"{flips.clean_cases} of {flips.cases} cases with no flipped run"
    )
    if flips.rate.mean != 0.0:
        run.fail("a scripted reviewer cannot flip")
    run.data["flips"] = {"cases": flips.cases, "rate": flips.rate.mean}


def dry_run(cards: CardSet | None = None, *, repeats_for_estimate: int = 3) -> DryRun:
    run = DryRun()
    run.say(BANNER)
    run.say()
    cards = cards or load_cards()
    _cards_section(run, cards)
    run.say()
    _hostile_section(run)
    run.say()
    _planted_section(run, cards)
    run.say()
    _flips_section(run)
    run.say()
    run.say("5. Cost estimates for the paid stages (arithmetic, nothing spent)")
    for line in costs.render(costs.all_stages(t1_plan(repeats_for_estimate))):
        run.say("   " + line if line else "")
    run.say()
    run.say("RESULT: " + ("the kit is consistent" if run.ok else "PROBLEMS FOUND, see above"))
    return run


def main(out: Callable[[str], None] = print, json_path: Path | None = None) -> int:
    """Print the dry run. With `json_path`, also write a small results file whose manifest says
    how many drafted labels nobody has reviewed and that the reviewer was scripted."""
    run = dry_run()
    for line in run.lines:
        out(line)
    if json_path is not None:
        unreviewed, drafted = run.data["cards"]["unreviewed"], run.data["cards"]["drafted"]
        document = {
            "kind": "source-review dry run",
            "scripted_reviewer": True,
            "note": BANNER,
            "ok": run.ok,
            "manifest": {"labels_unreviewed": unreviewed, "labels_drafted": drafted},
            "data": run.data,
        }
        json_path.write_text(json.dumps(document, indent=1) + "\n")
    return 0 if run.ok else 1


__all__ = ["DryRun", "dry_run", "main"]
