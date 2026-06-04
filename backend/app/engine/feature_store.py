"""
Feature store — thin persistence layer for the ML training dataset.

save_features()   : called by runner.py right after a signal is saved to DB
update_outcome()  : called by outcome_tracker.py when a signal resolves
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def save_features(signal_id: int, features: dict) -> None:
    """Persist the indicator snapshot for one signal. Non-fatal if it fails."""
    try:
        from sqlmodel import Session
        from app.models.db import SignalFeatures, engine

        row = SignalFeatures(signal_id=signal_id, **features)
        with Session(engine) as s:
            s.add(row)
            s.commit()
        logger.debug(f"Features saved for signal {signal_id}")
    except Exception as e:
        logger.warning(f"save_features failed for signal {signal_id}: {e}")


def update_outcome(
    signal_id: int,
    actual_pnl_pct: Optional[float],
    max_favorable_excursion: Optional[float],
    max_adverse_excursion: Optional[float],
    time_to_result_hours: float,
) -> None:
    """Fill outcome columns on the existing SignalFeatures row."""
    try:
        from sqlmodel import Session, select
        from app.models.db import SignalFeatures, engine

        with Session(engine) as s:
            feat = s.exec(
                select(SignalFeatures).where(SignalFeatures.signal_id == signal_id)
            ).first()
            if not feat:
                # No features row exists yet (e.g., old signal from before feature store).
                # Create a minimal row so outcome data is still captured.
                feat = SignalFeatures(signal_id=signal_id)
                s.add(feat)
                s.flush()

            feat.actual_pnl_pct = round(actual_pnl_pct, 3) if actual_pnl_pct is not None else None
            feat.max_favorable_excursion = round(max_favorable_excursion, 3) if max_favorable_excursion is not None else None
            feat.max_adverse_excursion = round(max_adverse_excursion, 3) if max_adverse_excursion is not None else None
            feat.time_to_result_hours = round(time_to_result_hours, 2)
            s.add(feat)
            s.commit()
        _pnl = f"{actual_pnl_pct:.2f}%" if actual_pnl_pct is not None else "None"
        _mfe = f"{max_favorable_excursion:.2f}%" if max_favorable_excursion is not None else "None"
        _mae = f"{max_adverse_excursion:.2f}%" if max_adverse_excursion is not None else "None"
        logger.debug(
            f"Outcome updated for signal {signal_id}: "
            f"pnl={_pnl} mfe={_mfe} mae={_mae} dur={time_to_result_hours:.1f}h"
        )
    except Exception as e:
        logger.warning(f"update_outcome failed for signal {signal_id}: {e}")
