# Smart Contract Analysis Agent

**Ask if a contract is safe to approve and get a risk report where every claim cites real evidence.**

Read-only (no transactions, no keys), Python, 2,400+ offline tests.

## Results

| What I measured | Result | Model |
|---|---|---|
| Finding the right audit knowledge (retrieval, recall@5) | **0.87** on 87 dev questions, **0.97** on 31 held-out | Qwen3-Embedding-8B |
| Claims backed by a real citation | **99.5%** of 1,004 claims in 87 agent runs | DeepSeek Flash |

**recall@5** is the share of right answers that land in the search's top 5, so 0.87 means 87% reach the model.

**Does re-reading code find more bugs?** Flags scored against the bugs Trail of Bits found in the same code:

| Set | System | Flags | Bugs found | Hit rate |
|---|---|---|---|---|
| Dev (62 bugs) | Plain model | 142 | 13.2 | 8.9% |
| | Plain + pipeline | 196 | 18.2 | 10.0% |
| | Plain + pipeline + verifier | 116 | 16.6 | 14.8% |
| Held-out (51 bugs) | Plain model | 200 | 8.0 | 6.0% |
| | Plain + pipeline | 353 | 9.0 | 4.0% |
| | Plain + pipeline + verifier | 246 | 6.0 | 4.5% |

**Outcome:** vs plain + pipeline the verifier cuts flags 30 to 41% but finds fewer bugs, and vs plain alone it wins only on dev.

*DeepSeek Flash throughout; dev is the mean of 5 runs, held-out is 1; hit rate is the share of flags matching a finding; the judging rubric is a draft, so exploratory.*

## How it works

```mermaid
flowchart TB
    Q(["Address + chain"]) --> T
    subgraph T["1. Tool calls"]
        direction LR
        A["Agent<br/>LLM + tools"] <--> F["Chain tools<br/>bytecode, proxies, selectors"]
        A <--> K["Knowledge search<br/>audit findings"]
        A --> V{"Citation<br/>validator"}
    end
    V --> E{"Enough<br/>evidence?"}
    E -- yes --> O["Cited risk report"]
    E -- "no: review the code" --> R
    subgraph R["2. Code review"]
        direction LR
        P["Plain model<br/>flags"] --> M["Join by<br/>place"]
        L["Pipeline<br/>flags"] --> M
        M --> W["Verifier checks<br/>every flag"] --> H["Flags for a<br/>person to review"]
    end
```

The agent defaults to Claude Sonnet 5.5 via OpenRouter; code review is built but not yet called by `python -m agent`.

## Evals

Retrieval and source review each have a dev set (tuned on) and a held-out set (rules fixed first, run once).

* **Dev set:** what I built and tuned on, so it flatters the verifier.
* **Held-out set:** fixed before any run and run once, but since used three times, so no longer sealed.
* **Retrieval quiz:** 125 questions drafted by Claude, split by id hash (30% held out): 87 dev, 38 held-out (31 read).
* **Source review:** 12 Trail of Bits audits kept by fixed rules, split by report: 5 dev (29 findings, 62 with Low and Informational), 7 held-out (22, 51).
* **Scoring:** three judge models vote on whether a flag matches a finding, and a gain needs a 95% interval that excludes zero.

Full tables and method: [`evals/benchmarks.md`](evals/benchmarks.md); every run, failures included: [`evals/results.md`](evals/results.md).

## Run it

```bash
uv sync && cp .env.example .env     # add your RPC, Etherscan and OpenRouter keys
make test                           # offline tests
python -m agent analyze --contract usdc    # a paid model call, a few cents
```

**Built:** chain tools, references, knowledge base, retrieval, agent, evals, source review. **Not built:** API, web UI, deployment.

Spec and working agreement: [`CLAUDE.md`](CLAUDE.md).

MIT licensed. Audit findings in [`knowledge/findings/`](knowledge/findings) are adapted from Trail of Bits reports under CC BY-SA 4.0 ([notice](knowledge/findings/NOTICE.md)).
