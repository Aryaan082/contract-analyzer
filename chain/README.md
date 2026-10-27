# chain/: read-only tools for looking at a contract on a blockchain

Component 1 of the contract analyzer. Each tool answers one question about an address and returns
`{provenance, data}`: **provenance** says where the answer came from (`chain`, `chain_id`,
`address`, `block`, `source`), **data** is the answer, or `{"error": {"code", "message"}}` if the
tool failed. Nothing here signs, sends or holds a key (`tests/test_no_signing.py` checks that).

```bash
uv run python -m chain profile 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2            # Ethereum
uv run python -m chain profile 0x4200000000000000000000000000000000000006 --chain base
uv run python -m chain profile <address> --block 20000000                            # an old block
```

`profile` runs every tool below on one address and prints one JSON document (about 4 seconds; most
of that is the explorer's rate limit). Exit status is 1 only if the code could not be read; a
failing tool shows up as an error in its own part and is named on stderr.

Setup is in `.env` (see `.env.example`): `RPC_URL_<CHAIN>` per chain, `ETHERSCAN_API_KEY`,
`DATABASE_URL`. Only the RPC URL is required; without the others the matching parts say so.

## The tools

| Question | Function | Needs | Returns |
|---|---|---|---|
| What is the code here? | `get_code` ([code.py](code.py)) | node | hash, size, whether saved; bytes only if asked |
| Does it forward calls elsewhere? | `resolve_proxy` ([proxy.py](proxy.py)) | node | kind, implementation, beacon, admin, every check made |
| What does one storage slot hold? | `get_storage` ([storage.py](storage.py)) | node | the 32 bytes, zero or not, an address guess |
| Which functions does it answer to? | `run_extract_selectors` ([selectors.py](selectors.py)) | code | 4-byte selectors with argument types |
| What are those functions called? | `lookup_selector` ([signatures.py](signatures.py)) | Sourcify, Postgres optional | canonical name, alternatives, labels |
| Is the source published? | `get_verified_source` ([explorer.py](explorer.py)) | Etherscan key | compiler settings, file list with hashes, text on request |
| Who deployed it? | `get_deployment` ([explorer.py](explorer.py)) | Etherscan key | deployer, creation tx and block, factory |
| What kind of thing is it? | `identify_contract` ([identify.py](identify.py)) | node | standard interfaces, protocol registries, which checks fit |
| Would a sell go through? | `simulate_sell` ([simulate.py](simulate.py)) | node, Foundry | buy/sell rehearsal on a local fork; separate command, not in `profile` |

All of them read **one pinned block**: `latest` is turned into a block number once per run, so the
code, the proxy slots and the selectors describe the same state and can be cited by block. The
node client remembers each `(address, block)` code read and `(address, slot, block)` slot read, so
tools that read the same thing during a run ask the node once.

### get_code and the code store

The bytes are returned only with `include_code=True`: up to 24,576 bytes is a lot of hex to read,
and the hash says what they are. Every bytecode read is **saved** in Postgres (`code_blob` by
hash, `code_at` by chain, address and block) and never expires, because a hash names exactly one
byte string and code at a block cannot change. It is a record, not a cache.

What it gives you: the exact bytes behind any cited analysis, even if the contract is later
destroyed or the node prunes the block; one copy per distinct bytecode (clones share it); and a
repeat read of the same address at a *settled* block (64+ behind the head) with no node call. It
cannot save the call for `latest`: only the node knows the code at a new block. Stored bytes are
re-hashed before use. The (address, block) record is written for every read but trusted only when
a reorganisation can no longer change that block.

### resolve_proxy

Follows one hop. Kinds: `eip1167-clone` (target written in the 45 bytes of code), `eip1967`,
`eip1967-beacon` (one `eth_call` to the beacon's `implementation()`), `legacy-oz` (USDC),
`unknown-proxy-like` (delegates calls, no recognised pointer: stETH), `not-a-proxy`, and
`undetermined`.

- Each slot read ends as **found** (an address with code), **absent**, **invalid** (junk, or an
  address with no code) or **error**. An error is never turned into "absent": nothing found plus an
  error is `undetermined`; something found plus an error is `partial`.
- A pointer is trusted only if the code contains DELEGATECALL, otherwise anyone could write a
  benign contract's address into a slot to borrow its look. The scan skips PUSH data and the
  compiler's metadata tail (WETH9 has a stray `0xf4` in its tail). Evidence, not proof.
- An empty admin slot does not mean nobody can upgrade (Aave V3's admin is fixed in code).
- Limits: one hop; canonical EIP-1167 only (not the PUSH0 variants); diamonds and Aragon-style
  proxies come out `unknown-proxy-like`, which also fits contracts that DELEGATECALL for other
  reasons.

### lookup_selector

A selector is four bytes, so a name for it is a claim anyone can forge. One batched request per
contract to Sourcify's 4byte database with `filter=false`, which returns spam-filtered names
marked instead of hiding them. Rules:

- canonical name = an unfiltered one, preferring a name seen in a verified contract;
- 2+ unfiltered names: label `ambiguous_selector`;
- a risky-sounding name among the alternatives (`mint` behind `transfer`'s selector): label
  `risky_alternative`, `high` if unfiltered, `low` if the database called it spam
  ([risky_names.py](risky_names.py); a test keeps it equal to `references/risk_patterns.py`);
- canonical name seen in no verified contract: label `weak_evidence`; nothing found:
  `unknown_selector`.

Names that are not shaped like a Solidity signature are dropped (they are untrusted text), and
spam is counted, not listed.

**Selector cache** (Postgres, `selector_status` one row per selector and `selector_name` one row
per candidate name): a lookup reads the cache first and asks the database only for what is missing
or older than 7 days. If the database is down, a stale entry is served and marked `stale`. If
Postgres is down, a read is a miss. The status table also records selectors the database did not
know, so they are not asked again for a week. Labels are recomputed from the stored names on read,
so changing the rules (bump `LABELS_VERSION`) needs no refetch.

### get_verified_source and get_deployment

Etherscan V2: one key, many chains (`chainid` comes from the node). The source and its names are
written by the contract's author, so they are **untrusted text**: the default answer is facts
(compiler, optimisation, files with size and SHA-256), and the text only with `include_source=True`,
capped at 40,000 characters, never interpreted. Verification can match *similar* bytecode, so a
proxy or clone may show another contract's source (`explorer_proxy_hint` carries the explorer's
claim). The deployer is the explorer's report, not re-checked on chain. Answers are for now, so
provenance has no block. The free plan covers Ethereum fully; on Base `get_deployment` was refused
as outside the plan when this was written (reported as `explorer_error`), `get_verified_source`
worked.

## Errors

A failed tool still returns the envelope. Codes: `chain_not_configured`, `invalid_address`,
`invalid_block`, `invalid_slot`, `invalid_bytecode`, `invalid_selector`, `rpc_unreachable`,
`rpc_error`, `state_unavailable` (node pruned the block), `lookup_unavailable`,
`explorer_not_configured`, `explorer_unavailable`, `explorer_rate_limited`, `explorer_error`,
`internal_error`. Messages are written in this package; raw text from a node, an explorer or a
revert is never passed on (it can hold an API key or text chosen by an attacker).

## Tests

```bash
uv run pytest                                   # offline: fakes for the node, Postgres and the web
uv run pytest -m live tests/test_chain_live.py  # real node, Sourcify, Etherscan, a throwaway schema
uv run pytest -m live tests/test_db_live.py     # the SQL against real Postgres
```

The live tests check the inspector's known contracts (WETH9, the zero address, Base WETH, Compound
USDC, Aave V3 Pool, an EIP-1167 clone, a beacon proxy, USDC, stETH) against sizes and hashes read
with Foundry's `cast`.

## Not done

- Nested proxies and the other proxy patterns above; who may upgrade is not resolved.
- Explorer-reported deployer is not re-checked against the chain.
- Etherscan-family explorers other than Etherscan V2 (Blockscout, Routescan).
- Inspector panels for the new tools.
