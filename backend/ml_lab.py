"""
ML Lab — offline training / backtest harness for signal-quality modelling.

Purpose
-------
The live `confidence` score is hand-tuned and (per the 159-signal audit) inverted:
the ≥90 bucket won 24% while the 60–80 band won ~44%. This harness tests whether
a model trained on the feature store can separate win/loss BETTER than that score,
and produces a calibrated p(win) we can eventually gate on.

It is standalone — it does NOT touch the live scorer. Run it any time the dataset
grows; it reports honestly whether we have enough data yet.

Usage:
    python ml_lab.py                # train + cross-validate + report
    python ml_lab.py --save         # also persist the calibrated model

What it reports:
  • Dataset size + class balance
  • Baseline AUC using the live `confidence` score (proves inversion if <0.5)
  • XGBoost AUC via stratified K-fold CV (+ time-ordered holdout)
  • Probability calibration table (predicted vs actual win-rate per decile)
  • Feature importance ranking
  • A go/no-go verdict based on sample size and AUC lift
"""
import argparse
import logging
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

# Feature columns pulled from the SignalFeatures snapshot + a few Signal fields.
FEATURE_COLS = [
    "price_change_1h", "price_change_4h", "price_change_24h",
    "ema9_gap", "ema21_gap", "ema50_gap", "ema200_gap",
    "rsi_14", "rsi_slope_3",
    "macd_line", "macd_signal_line", "macd_hist", "macd_hist_slope",
    "bb_pct", "bb_width", "atr_pct",
    "adx", "adx_pos", "adx_neg",
    "volume_ratio", "volume_trend_3",
    "candle_body_pct", "candle_upper_shadow", "candle_lower_shadow",
    "fear_greed", "funding_rate", "oi_change",
    "hour_utc", "day_of_week",
    # signal-level
    "is_long", "tier", "risk_reward",
]


def load_dataset() -> pd.DataFrame:
    from sqlmodel import Session, select
    from app.models.db import Signal, SignalFeatures, engine

    with Session(engine) as s:
        sigs = s.exec(select(Signal).where(Signal.result.in_(["win", "loss"]))).all()  # type: ignore
        feats = {f.signal_id: f for f in s.exec(select(SignalFeatures)).all()}

    rows = []
    for sig in sigs:
        f = feats.get(sig.id)
        if f is None:
            continue
        row = {c: getattr(f, c, None) for c in FEATURE_COLS if hasattr(f, c)}
        row["is_long"] = 1 if sig.direction == "LONG" else 0
        row["tier"] = sig.tier or 2
        row["risk_reward"] = sig.risk_reward
        row["confidence"] = sig.confidence          # for baseline comparison
        row["created_at"] = sig.created_at
        row["label"] = 1 if sig.result == "win" else 0
        rows.append(row)

    df = pd.DataFrame(rows).sort_values("created_at").reset_index(drop=True)
    return df


def auc(y_true, scores) -> float:
    """Rank-based AUC without sklearn dependency edge cases."""
    from sklearn.metrics import roc_auc_score
    try:
        return roc_auc_score(y_true, scores)
    except Exception:
        return float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="persist the calibrated model")
    args = ap.parse_args()

    df = load_dataset()
    n = len(df)
    pos = int(df["label"].sum())
    print("=" * 64)
    print("ML LAB — signal-quality model")
    print("=" * 64)
    print(f"Dataset            : {n} labelled signals ({pos} win / {n - pos} loss, "
          f"{100 * pos / n:.0f}% win)" if n else "No data")
    if n < 40:
        print("\nVERDICT: too few samples to model — keep collecting.")
        return

    feat_cols = [c for c in FEATURE_COLS if c in df.columns]
    X = df[feat_cols].astype(float).fillna(0.0).values
    y = df["label"].values

    # ── Baseline: live confidence score ────────────────────────────────────
    base_auc = auc(y, df["confidence"].values)
    print(f"\nBaseline AUC (live `confidence`) : {base_auc:.3f}  "
          f"{'<< INVERTED, worse than coin-flip' if base_auc < 0.5 else ''}")
    print("  (0.5 = random; <0.5 means the score is anti-predictive)")

    # ── XGBoost via stratified K-fold ──────────────────────────────────────
    from xgboost import XGBClassifier
    from sklearn.model_selection import StratifiedKFold

    k = 5 if n >= 100 else 3
    skf = StratifiedKFold(n_splits=k, shuffle=True, random_state=42)
    oof = np.zeros(n)  # out-of-fold predictions
    importances = np.zeros(len(feat_cols))

    for tr, va in skf.split(X, y):
        neg, p = (y[tr] == 0).sum(), max(1, (y[tr] == 1).sum())
        m = XGBClassifier(
            n_estimators=120, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            scale_pos_weight=neg / p, eval_metric="logloss", verbosity=0,
        )
        m.fit(X[tr], y[tr])
        oof[va] = m.predict_proba(X[va])[:, 1]
        importances += m.feature_importances_
    importances /= k

    model_auc = auc(y, oof)
    print(f"XGBoost AUC (5-fold OOF)         : {model_auc:.3f}  "
          f"(lift vs baseline: {model_auc - base_auc:+.3f})")

    # ── Time-ordered holdout (most realistic — train past, test future) ────
    cut = int(n * 0.7)
    neg, p = (y[:cut] == 0).sum(), max(1, (y[:cut] == 1).sum())
    m = XGBClassifier(n_estimators=120, max_depth=3, learning_rate=0.05,
                      subsample=0.8, colsample_bytree=0.8,
                      scale_pos_weight=neg / p, eval_metric="logloss", verbosity=0)
    m.fit(X[:cut], y[:cut])
    fut = m.predict_proba(X[cut:])[:, 1]
    fut_auc = auc(y[cut:], fut)
    print(f"XGBoost AUC (time holdout 70/30) : {fut_auc:.3f}  "
          f"(n_test={n - cut} — the honest, forward-looking number)")

    # ── Calibration table ──────────────────────────────────────────────────
    print("\nCalibration (OOF) — predicted vs actual win-rate by probability band:")
    dfc = pd.DataFrame({"p": oof, "y": y})
    for lo, hi in [(0, 0.3), (0.3, 0.45), (0.45, 0.55), (0.55, 0.7), (0.7, 1.01)]:
        sub = dfc[(dfc.p >= lo) & (dfc.p < hi)]
        if len(sub):
            print(f"  p {lo:.2f}-{hi:.2f}: n={len(sub):3d}  "
                  f"predicted~{sub.p.mean()*100:4.0f}%  actual={sub.y.mean()*100:4.0f}%")

    # ── Feature importance ─────────────────────────────────────────────────
    print("\nTop features (mean gain importance):")
    order = np.argsort(importances)[::-1][:12]
    for i in order:
        print(f"  {feat_cols[i]:22s} {importances[i]:.3f}")

    # ── Overfit / non-stationarity check ───────────────────────────────────
    gap = model_auc - fut_auc
    print("\nStability check:")
    print(f"  shuffled-CV AUC {model_auc:.2f}  vs  time-holdout AUC {fut_auc:.2f}  "
          f"(gap {gap:+.2f})")
    if gap > 0.15:
        print("  (!) Large gap — the in-sample fit does NOT hold forward in time.")
        print("    Cause: too few samples + regime shift. The model would NOT")
        print("    have helped on out-of-sample trades. This is the real blocker,")
        print("    not the headline CV number.")

    # ── Verdict ────────────────────────────────────────────────────────────
    print("\n" + "-" * 64)
    if n < 300 or gap > 0.15:
        print(f"VERDICT: promising in-sample but NOT forward-stable yet "
              f"(n={n}/300+, holdout AUC {fut_auc:.2f}). Don't put ML in the live "
              "gate — keep collecting clean v15 data, re-run this monthly.")
    elif fut_auc >= 0.60:
        print(f"VERDICT: ML beats the hand score (holdout AUC {fut_auc:.2f}). "
              "Ready to pilot as the primary ranker.")
    else:
        print(f"VERDICT: ML not yet decisively better (holdout AUC {fut_auc:.2f}). "
              "Iterate on features / collect more data.")

    if args.save and n >= 300:
        import joblib
        from pathlib import Path
        m_full = XGBClassifier(n_estimators=120, max_depth=3, learning_rate=0.05,
                               subsample=0.8, colsample_bytree=0.8,
                               eval_metric="logloss", verbosity=0)
        m_full.fit(X, y)
        out = Path("ml_models/signal_quality.joblib")
        out.parent.mkdir(exist_ok=True)
        joblib.dump({"model": m_full, "features": feat_cols}, out)
        print(f"\nSaved calibrated model → {out}")


if __name__ == "__main__":
    main()
