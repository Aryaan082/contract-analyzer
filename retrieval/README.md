# retrieval/: finding the reference cards that answer a question

Given a plain-language question and optional filters, return the best few **chunks** (one per
taxonomy entry or kept finding in `knowledge/`) with scores and citations. A chunk is a reference
card, not a piece of the contract being analysed; a high rank says "this card is relevant to the
question", never "this contract has this bug". The tools and the agent decide that.

```text
make models        download the small models once (about 150 MB, outside the repo)
make models-all    also the bigger pair used for the big-vs-small comparison (about 1.25 GB)
make index         embed every chunk with the default embedder and save the vectors; rebuild after any record changes
make index-local   the same with the small local model: no key, no network
make eval          run the retrieval quiz on the dev questions (see evals/)

python -m retrieval search "who can call this without being the owner?" -k 5
python -m retrieval search "drain the vault" --filter severity=high --filter record_type=finding
python -m retrieval search "an upgradeable contract nobody locked" --mode dense_rerank --json
```

## How a search works

1. **Filters** (`filters.py`) drop chunks that do not match: `record_type`, `severity`, `protocol`,
   `group`, `kind` (an entry's `applies_to`; `any` always matches), `date_from`, `date_to`.
2. **Meaning search** (`embedders.py`, `index.py`): every chunk was turned into a list of numbers
   by an embedding model when the index was built; the query gets one too (with the model's
   instruction prefix, set in `models.toml`) and the closest win. The vectors are held in memory and
   compared with every chunk (exact search). At tens to hundreds of chunks that takes microseconds;
   a database index would add setup and no speed.
3. **Rerank, optional** (`embedders.py`, mode `dense_rerank`): a cross-encoder reads the query and
   each of the top 50 candidates together and re-scores them. At 54 chunks the top 50 is almost
   everything, so here it acts as a second ranker; on a large corpus it would cap the cost at 50
   pairs per query. It was not shown to help and costs seconds, so it is off by default.

There is no word scoring: no keyword search, no tokenizer, no merging of two lists. They were built
and measured first (keyword search alone, hybrid) and removed on 2026-10-26 because they never beat
meaning search on the quiz and were weakest on code; the code and every logged row for them are at
commit `90d2cb1` (see `evals/results.md`).

`search.py` has the two modes (`dense`, `dense_rerank`), `Searcher`, and `open_searcher()` for
Component 5. **The default embedder is hosted** (Qwen3-Embedding-8B through OpenRouter,
`default_embedder` in `models.toml`; it needs `OPENROUTER_API_KEY` and network, and the query text
leaves the machine), and **the default mode is `dense`** (`DEFAULT_MODE`). Both need `make index`.
Hits are returned as data: use `render_quoted(hits)` to put them into a prompt, which prefixes every
line with `> ` and says the text is quoted, untrusted material. Everything in a chunk came from a
hand-written file or a PDF.

## Changing things

- **A different model.** Edit `models.toml` (add a tier or change a name; any model that fastembed
  lists works), then `python -m retrieval models --tier <tier>` and `make index`. Vectors are saved
  per model name, so two models never share a file. For a model fastembed does not have, write a
  class with the methods of `Embedder` / `Reranker` in `embedders.py` and pass it to `Searcher`.
- **A hosted embedding model.** Tiers with `provider = "openrouter"` in `models.toml` (`qwen8b`,
  `openai-large`, `gemini2`) call OpenRouter's `/embeddings` endpoint. They need `OPENROUTER_API_KEY`
  in `.env` (this checkout's or, from a git worktree, the main checkout's) or the shell, send the
  chunk text and the queries to OpenRouter, and cost cents for this corpus; the key is only ever put
  in the request header. `python -m evals.run retrieval --variants hosted --also dense-small`
  compares them with the local models. CI never runs them.
- **What goes in a chunk's text.** `recipes.toml` lists the fields per recipe (`default`,
  `with_examples`, `short`); `make index` and the evals take `--recipe`/the variant's recipe.
- **A record changed.** `make index`. Each index file stores a fingerprint of every chunk's text
  and the model name; if it no longer matches the records, `search` refuses to run and says so.

## Where things are

| | |
|---|---|
| `models.toml`, `recipes.toml` | which models, which fields (the only places to change those) |
| `chunks.py` | chunks and citations from Component 3's loaders |
| `index.py` | saving and loading vectors, the staleness check |
| `cli.py` | `models`, `index`, `search`; `models` is the only command that downloads |

Models are never downloaded by `index`, `search` or the evals; a missing one raises
`ModelMissing` with the command to run. Tests are offline and use fake models
(`tests/retrieval_support.py`); three tests that use the real small models carry the `models`
marker (`uv run pytest -m models`).

## Limits

- The corpus is 54 chunks (about 14k words) and fits in one prompt. Retrieval is built for the
  corpus it will grow into; at this size most variants score alike (see `evals/results.md`).
- Two findings (`TOB-AAVE-015`, `TOB-AAVE-7`) are longer than the 512 tokens the bge models read,
  so only their beginning is embedded with those models.
- Code-style queries are the weak spot: recall@5 0.76 for Qwen meaning search on dev, against 0.95
  for question-style queries, and 0.50 when the code is not from an indexed finding (8 questions).
  The agent should search with a description of what it found, not pasted code, until that improves.
- Reranker latency grows with the number of candidates and the model size (see the timing columns).
