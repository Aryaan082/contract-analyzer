"""From a card to something the reviewer can be run on.

A fixture card (planted, hostile) is a file we wrote: `scenario_for_card` reads it. A contract card
(incident, benign, grey) is a reference: the runner fetches its source at run time through
`review.bundle.fetch_source_bundle`, which needs the explorer key and a network, and is NOT done
here. `fetch_bundle` is that one call, used by the label drafting script and by the paid stages
after Aryaan approves them; nothing in the dry run calls it.
"""

from evals.source_review.cards import Card, ContractRef
from evals.source_review.hostile import Flaw, Scenario
from review.bundle import BundleFetch, fetch_source_bundle

# The bundle needs a code hash, which is read from the chain at the pinned block. The label drafting
# script only reads source, so it passes this placeholder and never stores the bundle.
UNREAD_CODE_HASH = "0x" + "00" * 32


def flaws_of(card: Card) -> tuple[Flaw, ...]:
    """The scripted reviewer's reply for a card: one item per labelled function, under its first
    labelled entry. Only the scripted gates use it; a real reviewer is never told the label."""
    return tuple(Flaw(f.contract, f.function, f.entries[0]) for f in card.functions)


def scenario_for_card(card: Card, *, flag_labels: bool = True) -> Scenario:
    """The scenario of a fixture card. Raises for a card that refers to a contract on a chain."""
    path = card.fixture_path
    if path is None:
        raise ValueError(f"{card.id} is a contract reference; its source is fetched at run time")
    return Scenario(
        files=((f"contracts/{path.stem}.sol", path.read_text()),),
        flaws=flaws_of(card) if flag_labels else (),
    )


def fetch_bundle(explorer, ref: ContractRef, *, code_hash: str = UNREAD_CODE_HASH) -> BundleFetch:
    """One explorer fetch for a contract card (read-only; the key comes from `.env`)."""
    return fetch_source_bundle(
        explorer,
        None,
        chain=ref.chain,
        chain_id=ref.chain_id,
        address=ref.fetch_address,
        block=ref.block,
        code_hash=code_hash,
    )
