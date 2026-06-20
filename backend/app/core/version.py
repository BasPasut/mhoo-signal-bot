"""
Algorithm version — single source of truth.

Bump ALGO_VERSION whenever the signal-generation logic changes in a way that
makes new signals NOT comparable to old ones (new gates, re-weighted scoring,
different SL/TP math). Every signal is stamped with the version that produced
it, so the Performance page can segment data by version and old regimes never
contaminate the metrics of the current algo.

Keep CHANGELOG entries short — they surface in the version picker UI.
"""

ALGO_VERSION = "v15"

# version → one-line description shown in the Performance version picker
ALGO_CHANGELOG = {
    "v15": "Market-context gate — no SHORT into capitulation (FNG<20), trend-establishment via EMA200",
    "legacy": "Pre-v15 signals (mixed v9–v14 logic, not directly comparable)",
}
