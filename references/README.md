# references/: the reference contract library

Known contracts to compare an unknown contract against. Given a contract's code and its list of
function selectors, it answers: which known contract does this resemble, what does it add, and
which of those additions are the risky kind.

```text
python -m references list                              the library and its counts
python -m references match --code @code.hex          selectors are read from the code (evmole)
python -m references diff oz-erc20 --code @code.hex [--names @names.json]
python -m references risk --code @code.hex [--names @names.json]
    (any of them also takes --selectors @sel.txt to give the selectors by hand)
python -m references add --id usdc-ethereum --name "USD Coin" --family usdc \
    --chain ethereum --address 0x... --env-file ../.env       fetch, check and save a deployed contract
```

## What is in it

`references/data/*.json`, one file per entry, validated on load (selectors must be the hash of their
signatures, code must hash to `code_hash`, every listed function must appear in the code).

- **Compiled** from OpenZeppelin v5.7.0 (pinned by commit) with solc 0.8.28, optimizer 200 runs:
  ERC20, ERC721, Ownable and Pausable (modules: function list only), ERC1967Proxy,
  TransparentUpgradeableProxy, BeaconProxy, UpgradeableBeacon. Our wrapper contracts are in
  `references/dev/forge/src/refs/`.
- **Deployed**, fetched once with `add` and pinned to a block: WETH9, Base WETH, a Uniswap V2 pair,
  USDT, DAI, UNI. Their ABIs come from Sourcify (free, no API key) and are checked against the code.
- **From a standard**: the EIP-1167 minimal proxy.

Rebuild the compiled ones with `python -m references.dev.build` (needs Foundry; fetches the pinned
OpenZeppelin tag into the git-ignored `references/.cache/` if missing). Tests never need either.

## How a comparison works

1. **Exact copy** (strongest, cannot be faked): the target's code equals a reference's, ignoring the
   compiler's metadata tail and bytes that vary between copies (immutables, a clone's embedded
   address). Identical code behaves identically.
2. **Function list**: how much of the reference's function list the target has (`reference_coverage`),
   and how many extras. Independent of compiler.
3. **Code constants** (rough hint): event topics, error selectors and strings found in both.

`diff` names the extras from the library first (`owner()` is Ownable's), then from outside
candidates, which are checked to hash to the selector. `risk` flags capabilities (mint, blacklist,
fee setters, pause, owner withdraw, upgrade, selfdestruct/delegatecall) by evidence strength and
says whether the matched reference has them too. It never says who can call a function.

## Different compilers give different bytes

The same source compiled with another solc version or optimizer setting produces different
bytecode. Measured on a hand-written ERC20 (3 solc versions x optimizer on/off), comparing one
build against the others (average over pairs; "constants" is what `match` reports):

| Comparison | opcode-sequence overlap | constants overlap |
|---|---|---|
| same source, other compiler or optimizer | 0.55 (as low as 0.31) | 0.79 (as low as 0.64) |
| + mint and blacklist, same settings | 0.85 | 0.75 |
| transfer rigged, same settings | 0.91 | 0.95 |
| + mint and blacklist, other settings | 0.48 | 0.60 |

So opcode sequences were dropped in favour of constants (more stable across compilers), and neither
can be used as a verdict: "same source, other compiler" (0.79) overlaps with "modified, same
compiler" (0.75). Code similarity is a hint; the exact-copy check and the function list carry the
weight. What `match` does about it: it reports both compiler versions (read from the metadata tail)
so a reader sees when a comparison is unlike for like, and canonical deployed contracts are matched
by their exact bytes.

Known limits: a function list shows the same interface, not the same behaviour (a rigged
`transfer` has the same list); a name for a selector is a candidate (anyone can make a name hash
to a chosen selector); an opcode hit comes from a linear scan and is evidence, not proof; thresholds
in `match.py` are provisional until Component 6 has labelled data.

## Seam to `chain/`

Only `tools.py` and `acquire.py` import `chain/`. `chain.selectors.extract_selectors` (evmole) is
wired in: give code and no selectors, and they are read from it. `get_code` (chain slice 2) and
`lookup_selector` (slice 5) are not built yet; when they exist, a small adapter feeds their results
into `tools.run_*` (code hex, `{selector: [NameCandidate]}`) and forwards `get_code`'s provenance.
The risky-name list in `risk_patterns.py` must be unified with the one `chain/` will have for
selector lookup (`chain/` cannot import `references/`, so the shared list belongs in `chain/`).
`bytecode.py` is the walker chain slice 4 needs for its DELEGATECALL check; it should move to
`chain/` then, and `references/` import it.
