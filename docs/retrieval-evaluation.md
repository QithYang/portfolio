# Retrieval evaluation (October 2026)

Pink Anchor ranks memories with a hybrid model: BM25 full-text relevance, embedding similarity and rule-based reranking. This test asked two questions: does the hybrid model beat full-text search alone, and does every query need the embedding call?

| Metric | Before | After | Difference (95% CI) |
|---|---|---|---|
| nDCG@5, hybrid vs BM25 (44 queries with relevant memories) | 0.602 | 0.687 | +0.086 (+0.020 to +0.150) |
| nDCG@5, gated vs full hybrid | 0.687 | 0.683 | −0.005 (−0.023 to +0.011) |
| Embedding call rate (73 queries) | 100% | 77% | −23% |
| Mean latency (73 queries) | 655 ms | 507 ms | −148 ms (−212 to −88) |

How it was tested
- 73 real queries from daily use. Queries and results were frozen, and the gate's test plan was written down before any gate numbers were run; the final figures re-run that plan unchanged on the final labels.
- All relevance labels were assigned by hand; 53 were re-labelled blind in a second round by the same annotator (intra-rater weighted κ = 0.79, 95% CI 0.64 to 0.89).
- Confidence intervals from a paired bootstrap; resampling by topic cluster gives the same conclusion.
- The gate threshold was fitted on one half of the queries and tested on the other (2-fold cross-validation).

Decision: the confidence gate is now live, since it saves about a quarter of embedding calls without a measurable loss in ranking quality.

Next: about 40% of logged queries asked for content the memory store does not hold, mostly material in archived documents. Indexing those documents is the next step.
