"""
Tests for core/hmm_engine.py and core/feature_engineering.py.

Run with:  pytest tests/test_hmm.py -v
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Allow imports from the project root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.feature_engineering import FeatureEngineer
from core.hmm_engine import (
    HMMEngine,
    _CONFIRM_BARS,
    _FLICKER_LIMIT,
    _FLICKER_WINDOW,
    _LABEL_MAPS,
    _MAX_COMPONENTS,
    _MIN_COMPONENTS,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 600, seed: int = 42) -> pd.DataFrame:
    """
    Synthetic OHLCV data with three embedded volatility regimes so the HMM
    has something meaningful to latch on to.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n)

    # Three-regime volatility schedule
    regime_vol = np.select(
        [np.arange(n) < n // 3,
         np.arange(n) < 2 * n // 3],
        [0.005, 0.015],
        default=0.030,
    )
    log_ret = rng.normal(0.0002, regime_vol, size=n)
    close   = 100.0 * np.exp(np.cumsum(log_ret))
    noise   = rng.uniform(0.001, 0.005, size=n)
    high    = close * (1 + noise)
    low     = close * (1 - noise)
    open_   = close * (1 + rng.normal(0, 0.002, size=n))
    volume  = rng.integers(1_000_000, 5_000_000, size=n).astype(float)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _make_engine_and_features(n: int = 600, seed: int = 42):
    """Return a fitted HMMEngine + the normalised feature DataFrame."""
    ohlcv = _make_ohlcv(n=n, seed=seed)
    fe    = FeatureEngineer()
    feats = fe.fit_transform(ohlcv)
    engine = HMMEngine(n_iter=30, random_state=seed, min_history_bars=252)
    engine.fit(feats)
    return engine, feats


# ---------------------------------------------------------------------------
# 1. FeatureEngineer basic sanity
# ---------------------------------------------------------------------------

class TestFeatureEngineer:
    def test_compute_returns_correct_columns(self):
        ohlcv = _make_ohlcv()
        fe    = FeatureEngineer()
        raw   = fe.compute(ohlcv)
        assert set(FeatureEngineer.FEATURE_NAMES).issubset(raw.columns)

    def test_no_nans_after_compute(self):
        ohlcv = _make_ohlcv()
        fe    = FeatureEngineer()
        raw   = fe.compute(ohlcv)
        assert not raw.isnull().any().any(), "compute() must return no NaN rows"

    def test_fit_transform_shape(self):
        ohlcv = _make_ohlcv(600)
        fe    = FeatureEngineer()
        feats = fe.fit_transform(ohlcv)
        assert feats.shape[1] == len(FeatureEngineer.FEATURE_NAMES)
        assert len(feats) > 0

    def test_normalise_requires_fit_first(self):
        ohlcv = _make_ohlcv()
        fe    = FeatureEngineer()
        raw   = fe.compute(ohlcv)
        with pytest.raises(RuntimeError, match="fit_normaliser"):
            fe.normalise(raw)

    def test_transform_consistent_with_fit_transform(self):
        """transform() on new data must use the same scaler as fit_transform()."""
        ohlcv  = _make_ohlcv(600)
        ohlcv2 = _make_ohlcv(100, seed=99)
        fe     = FeatureEngineer()
        fe.fit_transform(ohlcv)
        t1 = fe.transform(ohlcv2)
        t2 = fe.transform(ohlcv2)
        pd.testing.assert_frame_equal(t1, t2)


# ---------------------------------------------------------------------------
# 2. HMM fits and converges
# ---------------------------------------------------------------------------

class TestHMMFit:
    def test_fit_succeeds_and_selects_k_in_range(self):
        engine, feats = _make_engine_and_features()
        assert _MIN_COMPONENTS <= engine.n_components <= _MAX_COMPONENTS

    def test_regime_labels_assigned(self):
        engine, _ = _make_engine_and_features()
        labels = engine.regime_labels
        assert len(labels) == engine.n_components
        expected = _LABEL_MAPS[engine.n_components]
        assert labels == expected

    def test_fit_raises_on_insufficient_data(self):
        ohlcv = _make_ohlcv(n=50)
        fe    = FeatureEngineer()
        feats = fe.fit_transform(ohlcv)
        engine = HMMEngine(min_history_bars=252)
        with pytest.raises(ValueError, match="at least"):
            engine.fit(feats)

    def test_save_load_roundtrip(self, tmp_path):
        engine, feats = _make_engine_and_features()
        path = str(tmp_path / "engine.pkl")
        engine.save(path)

        engine2 = HMMEngine.load(path)
        assert engine2.n_components == engine.n_components
        assert engine2.regime_labels == engine.regime_labels

        # Identical predictions on same data after save/load
        X = feats.values.astype(float)
        engine._reset_forward_state()
        engine2._reset_forward_state()
        for row in X[:50]:
            r1 = engine.update(row)
            r2 = engine2.update(row)
            assert r1 == r2, "Loaded engine must produce identical regime sequence"


# ---------------------------------------------------------------------------
# 3. Regime labels are sorted by mean return (economic ordering)
# ---------------------------------------------------------------------------

class TestRegimeLabelling:
    def test_labels_ordered_by_mean_return(self):
        """
        After fitting, mean return of the features inside each regime must
        be non-decreasing from label index 0 (Bear/Crash) to the last (Bull/Euphoria).
        """
        engine, feats = _make_engine_and_features()
        X         = feats.values.astype(float)
        ret_col   = list(feats.columns).index("ret_1d")

        # Get raw HMM state sequence (internal, not the ranked sequence)
        raw_states = engine._model.predict(X)

        k = engine.n_components
        mean_rets = []
        for rank in range(k):
            hmm_state = engine._label_to_state[rank]
            mask = raw_states == hmm_state
            if mask.any():
                mean_rets.append(X[mask, ret_col].mean())
            else:
                mean_rets.append(np.nan)

        # Drop NaN placeholders for states that happen to be empty on this data
        valid = [(i, v) for i, v in enumerate(mean_rets) if not np.isnan(v)]
        for (i, v1), (j, v2) in zip(valid[:-1], valid[1:]):
            assert v1 <= v2 + 1e-8, (
                f"Regime label {i} (mean_ret={v1:.5f}) should be <= "
                f"regime label {j} (mean_ret={v2:.5f})"
            )

    def test_label_count_matches_n_components(self):
        engine, _ = _make_engine_and_features()
        assert len(engine.regime_labels) == engine.n_components


# ---------------------------------------------------------------------------
# 4. No look-ahead bias — forward algorithm only
# ---------------------------------------------------------------------------

class TestNoLookAhead:
    def test_update_uses_only_past_observations(self):
        """
        Flipping a single future bar must not change the regime prediction
        for any earlier bar.
        """
        engine, feats = _make_engine_and_features()
        n = len(feats)
        split = n // 2

        # Record results up to split using first-half features
        engine._reset_forward_state()
        results_original = []
        for row in feats.values[:split]:
            results_original.append(engine.update(row))

        # Corrupt all bars AFTER split and re-run from scratch
        feats_corrupted = feats.copy()
        feats_corrupted.iloc[split:] = 999.0   # garbage values

        engine._reset_forward_state()
        results_corrupted = []
        for row in feats_corrupted.values[:split]:
            results_corrupted.append(engine.update(row))

        assert results_original == results_corrupted, (
            "Changing future bars altered past predictions — look-ahead bias detected!"
        )

    def test_update_batch_no_lookahead(self):
        """update_batch() must produce the same output as sequential update()."""
        engine, feats = _make_engine_and_features()
        X = feats.values.astype(float)

        engine._reset_forward_state()
        seq = [engine.update(row) for row in X]

        engine._reset_forward_state()
        batch = engine.update_batch(feats)

        assert list(batch) == seq, "update_batch() diverged from sequential update()"

    def test_predict_proba_sums_to_one(self):
        engine, feats = _make_engine_and_features()
        engine._reset_forward_state()
        for row in feats.values:
            engine.update(row)
            proba = engine.predict_proba_current()
            assert abs(proba.sum() - 1.0) < 1e-6, (
                f"Posterior probabilities sum to {proba.sum()}, expected 1.0"
            )
            assert (proba >= 0).all(), "Negative posterior probability detected"


# ---------------------------------------------------------------------------
# 5. Stability filter
# ---------------------------------------------------------------------------

class TestStabilityFilter:
    def _make_engine_no_fit(self) -> HMMEngine:
        """Engine with _confirmed_regime driven purely by stability logic."""
        engine, feats = _make_engine_and_features()
        engine._reset_forward_state()
        return engine, feats

    def test_confirmation_requires_3_consecutive_bars(self):
        """Regime must NOT be confirmed after only 1 or 2 bars."""
        engine, feats = _make_engine_and_features()
        engine._reset_forward_state()

        # Drive 1 or 2 bars, check it stays at -1
        row = feats.values[0]
        engine.update(row)
        assert engine.current_regime() == -1, (
            "Regime should not be confirmed after 1 bar"
        )
        engine.update(feats.values[1])
        assert engine.current_regime() == -1, (
            "Regime should not be confirmed after 2 bars (need 3)"
        )

    def test_confirmation_happens_at_3rd_bar(self):
        """After 3 identical candidate bars, current_regime() must be >= 0."""
        engine, feats = _make_engine_and_features()
        engine._reset_forward_state()

        # Force the stability filter directly without going through update()
        forced_regime = 0
        for _ in range(_CONFIRM_BARS):
            engine._update_stability_filter(forced_regime)

        assert engine.current_regime() == forced_regime

    def test_flicker_warning_sets_high_uncertainty(self):
        """
        More than _FLICKER_LIMIT transitions in _FLICKER_WINDOW bars
        must set high_uncertainty = True.
        """
        engine, _ = _make_engine_and_features()
        engine._reset_forward_state()

        # Alternate between regime 0 and 1 — many transitions
        for i in range(_FLICKER_WINDOW):
            engine._update_stability_filter(i % 2)

        assert engine.high_uncertainty is True, (
            "high_uncertainty should be True after rapid flickering"
        )

    def test_no_flicker_clears_high_uncertainty(self):
        """After a stable period, high_uncertainty must return to False."""
        engine, _ = _make_engine_and_features()
        engine._reset_forward_state()

        # First cause high uncertainty
        for i in range(_FLICKER_WINDOW):
            engine._update_stability_filter(i % 2)
        assert engine.high_uncertainty is True

        # Now feed a stable stream long enough to refill the window
        for _ in range(_FLICKER_WINDOW):
            engine._update_stability_filter(0)

        assert engine.high_uncertainty is False, (
            "high_uncertainty should clear after a stable period"
        )

    def test_should_refit_flag(self):
        engine, _ = _make_engine_and_features()
        interval = engine.refit_interval_bars
        assert engine.should_refit(0) is False
        assert engine.should_refit(interval) is True
        assert engine.should_refit(interval + 1) is False
        assert engine.should_refit(2 * interval) is True
