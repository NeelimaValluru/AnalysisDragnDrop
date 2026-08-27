# ApiIntentMatch

`analysis-gui-cli similar` ranks discovered library **chunks** (functions, methods, inline blocks) so a scientist can find “code like this step.”

## What is actually novel

Clone detectors (MOSS, SourcererCC) typically discard comments and fingerprint tokens. Lab notebooks do the opposite: comments say *what*, and the *how* is a short sequence of library calls (`signal.butter` then `signal.sosfilt`). ApiIntentMatch keeps **both** views, adds a cheap neural/data-kind prior, and scores call **order** with a short Smith–Waterman pass on inverted-index candidates only.

There is **no** learned embedding model in the default path, no CFG, and no Type-4 semantic clone detector. Optional OpenAI rerank, when a caller enables it, reranks the top 50 of *this* candidate set; it does not replace it.

## Wrapper inheritance

- Same-file helpers (`process_traces` → `_apply_sos`) inherit the callee’s API sequence, depth ≤ 2.
- **One-hop relative imports** in the same package (`from .filters import bandpass` in module A) inherit `bandpass`’s sequence (depth 1–2). Absolute imports and `import *` are skipped so the walk cannot explode.

## Query shapes

```bash
analysis-gui-cli similar "bandpass eeg filter" --root ./src
analysis-gui-cli similar --from-span path.py:12-40 --root ./src
analysis-gui-cli similar --from-kind repo.module.func --root ./src
analysis-gui-cli similar "..." --legacy-tfidf    # old TF-IDF + Jaccard
```

Implementation: `analysis_gui.repository.matching`.
