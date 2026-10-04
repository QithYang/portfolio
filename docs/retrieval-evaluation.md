# Retrieval evaluation (October 2026)

Pink Anchor ranks memories with a hybrid model: BM25 full-text relevance, embedding similarity and rule-based reranking. This test asked two questions: does the hybrid model beat full-text search alone, and does every query need the embedding call?

| Metric | Before | After | Difference (95% CI) |
|---|---|---|---|
| nDCG@5, hybrid vs BM25 (44 queries with relevant memories) | 0.602 | 0.687 | +0.086 (+0.020 to +0.150) |
| nDCG@5, gated vs full hybrid (same 44 queries) | 0.687 | 0.683 | −0.005 (−0.023 to +0.011) |
| Embedding call rate (73 queries) | 100% | 77% | −23% |
| Mean latency (73 queries) | 656 ms | 507 ms | −149 ms (−212 to −88) |

Differences are computed before rounding. nDCG@5 is only defined for queries with at least one relevant memory, so it uses 44 of the 73 queries; call rate and latency use all 73.

## How it was tested

- 73 real search queries logged in daily use, issued by the AI assistants that use the service. Every method's top five results were pooled and labelled 0, 1 or 2 for relevance under a written rubric, without knowing which method returned them.
- The labels were checked against 53 blind human judgments on a random sample of 8 queries: weighted κ = 0.79 (95% CI 0.64 to 0.89).
- Queries and results were frozen, and the gate's test plan (signal, threshold rule, data split) was written down before any gate numbers were run. The final figures re-run that plan unchanged on the final labels.
- Confidence intervals come from a paired bootstrap with 10,000 resamples. For the hybrid-vs-BM25 result, resampling whole topic clusters instead of single queries gives the same conclusion (+0.019 to +0.175).
- The gate skips the embedding call when the top rule-based score is high. Its threshold was fitted on one half of the queries and tested on the other, then the other way round (2-fold cross-validation), so the gate figures are out of sample. Latency is estimated from each query's measured timings on both paths.

## Decision

The confidence gate is now live. It saves about a quarter of embedding calls without a measurable loss in ranking quality. The live threshold was refitted on all 73 queries by the same rule.

## Next

29 of the 73 queries (40%) found no relevant memory in any method's top five. Many of them ask for material kept in archived documents rather than in the memory store. Indexing those documents, then re-testing on new queries, is the next step.
