"""Keyword scoring: Okapi BM25.

Every word gets a weight for how rare it is across all chunks (its inverse document frequency),
and a chunk's score is the sum, over the query's words, of that weight times how often the chunk
uses the word. Two things keep it fair: a word repeated many times counts for diminishing
returns (`k1`), and a long chunk is discounted against the average length (`b`).

    idf(t)      = ln(1 + (N - n_t + 0.5) / (n_t + 0.5))        N chunks, n_t of them contain t
    score(q, d) = sum over distinct t in q of
                  idf(t) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(d) / avg_len))

This is the Lucene form of the weight (never negative). `k1 = 1.5` and `b = 0.75` are the
textbook defaults and are not tuned on the quiz. Postgres `ts_rank` is not BM25: its documentation
says it uses no corpus-wide information, so it cannot weigh a rare word above a common one.
"""

import math
from collections import Counter
from collections.abc import Sequence


class BM25:
    def __init__(
        self, documents: Sequence[Sequence[str]], *, k1: float = 1.5, b: float = 0.75
    ) -> None:
        self.k1, self.b = k1, b
        self._tf = [Counter(doc) for doc in documents]
        self._len = [len(doc) for doc in documents]
        self._avg_len = (sum(self._len) / len(self._len)) if self._len else 0.0
        containing: Counter[str] = Counter()
        for tf in self._tf:
            containing.update(tf.keys())
        n = len(documents)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in containing.items()}

    def scores(self, query: Sequence[str]) -> list[float]:
        """One score per document, in document order; 0.0 where no query word occurs."""
        terms = [t for t in dict.fromkeys(query) if t in self.idf]
        out = []
        for tf, length in zip(self._tf, self._len, strict=True):
            norm = self.k1 * (1 - self.b + self.b * length / self._avg_len) if length else 0.0
            score = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    score += self.idf[t] * f * (self.k1 + 1) / (f + norm)
            out.append(score)
        return out
