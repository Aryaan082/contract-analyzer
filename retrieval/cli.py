"""Command line: `python -m retrieval <command>`.

models   download the models named in models.toml (--tier small|base|all); the only command that
         ever downloads anything
index    embed every chunk and save the vectors (`make index`); prints how long it took and how
         many chunks are longer than the model reads
search   run one query and print the hits (--mode, --filter key=value, --json)
"""

import argparse
import json
import sys
import time
from collections.abc import Sequence

from knowledge.taxonomy import TaxonomyError
from retrieval.chunks import RecipeError, build_chunks
from retrieval.embedders import (
    ModelMissing,
    get_embedder,
    get_reranker,
    load_models_config,
)
from retrieval.filters import Filters
from retrieval.index import IndexUnavailable, build_index, keyword_index, load_index
from retrieval.search import DEFAULT_MODE, MODES, Searcher, citation_text, render_quoted

MAX_MODEL_TOKENS = 512  # what the BGE models read; longer chunks are cut


def _models(args: argparse.Namespace) -> int:
    config = load_models_config()
    tiers = (
        [args.tier] if args.tier != "all" else sorted(set(config.embedder) | set(config.reranker))
    )
    for tier in tiers:
        if tier in config.embedder and config.embedder[tier].provider == "openrouter":
            print(f"embedder {tier:6} {config.embedder[tier].name} is hosted: nothing to download")
        elif tier in config.embedder:
            started = time.perf_counter()
            embedder = get_embedder(tier, allow_download=True)
            embedder.embed_passages(["warm-up"])
            print(f"embedder {tier:6} {embedder.name} ready ({time.perf_counter() - started:.1f}s)")
        if tier in config.reranker:
            started = time.perf_counter()
            reranker = get_reranker(tier, allow_download=True)
            reranker.score("warm-up", ["warm-up"])
            print(f"reranker {tier:6} {reranker.name} ready ({time.perf_counter() - started:.1f}s)")
    return 0


def _index(args: argparse.Namespace) -> int:
    embedder = get_embedder(args.embedder)
    chunks = build_chunks(args.recipe)
    index = build_index(embedder, args.recipe, chunks=chunks)
    words = [len(c.text.split()) for c in chunks]
    print(
        f"{len(chunks)} chunks ({sum(1 for c in chunks if c.record_type == 'taxonomy')} entries, "
        f"{sum(1 for c in chunks if c.record_type == 'finding')} findings), {sum(words)} words, "
        f"longest {max(words)} words"
    )
    if hasattr(embedder, "token_count"):  # local models only
        counts = [embedder.token_count(c.text) for c in chunks]
        cut = [c.id for c, n in zip(chunks, counts, strict=True) if n > MAX_MODEL_TOKENS]
        print(f"{len(cut)} chunks are longer than {MAX_MODEL_TOKENS} tokens and are cut")
        for chunk_id in cut:
            print(f"  cut: {chunk_id}")
    print(f"embedded with {embedder.name} in {index.build_seconds:.2f}s, recipe {args.recipe}")
    if hasattr(embedder, "spent"):
        spent = embedder.spent
        print(
            f"hosted: {spent['requests']} requests, {spent['tokens']} tokens, cost {spent['cost']}"
        )
    return 0


def _filters(pairs: Sequence[str]) -> Filters | None:
    values = {}
    for pair in pairs:
        key, _, value = pair.partition("=")
        values[key] = value
    return Filters(**values) if values else None


def _search(args: argparse.Namespace) -> int:
    embedder = get_embedder(args.embedder) if args.mode != "bm25" else None
    index = keyword_index(args.recipe) if embedder is None else load_index(embedder, args.recipe)
    reranker = get_reranker(args.reranker) if args.mode == "hybrid_rerank" else None
    searcher = Searcher(index, embedder=embedder, reranker=reranker, tokenizer=args.tokenizer)
    started = time.perf_counter()
    result = searcher.search(args.query, k=args.k, filters=_filters(args.filter), mode=args.mode)
    elapsed = time.perf_counter() - started
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        return 0
    for hit in result.hits:
        stages = " ".join(f"{name}={score:.3f}" for name, score in hit.scores.items())
        print(f"{hit.rank:2}. {hit.chunk.id}  [{hit.chunk.record_type}]  {hit.chunk.title}")
        print(f"      {stages}")
        print(f"      cite: {citation_text(hit.chunk)}")
    print(f"{len(result.hits)} hits in {elapsed * 1000:.0f} ms ({args.mode})")
    if args.quoted:
        print()
        print(render_quoted(result.hits))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m retrieval", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    models = commands.add_parser("models", help="download the models (the only command that does)")
    models.add_argument(
        "--tier", default="small", help="small (default), base, all, or a tier name"
    )
    models.set_defaults(run=_models)

    index = commands.add_parser("index", help="embed every chunk and save the vectors")
    index.add_argument(
        "--embedder", default=None, help="tier or model name; default from models.toml"
    )
    index.add_argument("--recipe", default="default")
    index.set_defaults(run=_index)

    search = commands.add_parser("search", help="run one query")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=5)
    search.add_argument("--mode", choices=MODES, default=DEFAULT_MODE)
    search.add_argument("--filter", action="append", default=[], metavar="KEY=VALUE")
    search.add_argument("--embedder", default=None)
    search.add_argument("--reranker", default=None)
    search.add_argument("--recipe", default="default")
    search.add_argument("--tokenizer", choices=("identifier", "plain"), default="identifier")
    search.add_argument("--json", action="store_true")
    search.add_argument("--quoted", action="store_true", help="also print the quoted prompt text")
    search.set_defaults(run=_search)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (ModelMissing, IndexUnavailable, RecipeError, TaxonomyError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
