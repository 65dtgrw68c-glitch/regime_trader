"""
Feature Engineering — build the HMM input feature matrix from raw OHLCV data.

Input DataFrame must have columns: open, high, low, close, volume
(case-insensitive).  The index must be a DatetimeIndex.

Output of .compute() is a DataFrame of normalised features with the same
index, NaN rows for the warm-up period removed.
"""

import numpy as np
import pandas as pd
from sklearn.preprocessing import RobustScaler


# Features that require these many bars of history before the first valid row.
_WARM_UP_BARS = 22   # driven by 21-day rolling windows


class FeatureEngineer:
    """Compute and normalise technical features used as HMM observations."""

    # Canonical ordered list — order matters for the HMM covariance matrix.
    FEATURE_NAMES: list[str] = [
        "ret_1d",
        "ret_5d",
        "ret_21d",
        "log_ret_1d",
        "realised_vol_5d",
        "realised_vol_21d",
        "rsi_14",
        "atr_pct_14",
        "volume_zscore_21d",
        "parkinson_vol_10d",
    ]

    def __init__(self) -> None:
        self._scaler = RobustScaler()
        self._scaler_fitted = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """
        Compute raw (un-normalised) features from an OHLCV DataFrame.

        Returns a DataFrame aligned with the input index; the first
        _WARM_UP_BARS rows are NaN and are dropped before returning.
        """
        df = self._normalise_columns(ohlcv)
        out = pd.DataFrame(index=df.index)

        close = df["close"]
        high  = df["high"]
        low   = df["low"]
        vol   = df["volume"]

        log_ret = np.log(close / close.shift(1))

        out["ret_1d"]  = close.pct_change(1)
        out["ret_5d"]  = close.pct_change(5)
        out["ret_21d"] = close.pct_change(21)
        out["log_ret_1d"] = log_ret

        out["realised_vol_5d"]  = log_ret.rolling(5).std()  * np.sqrt(252)
        out["realised_vol_21d"] = log_ret.rolling(21).std() * np.sqrt(252)

        out["rsi_14"] = self._rsi(close, 14)

        out["atr_pct_14"] = self._atr(high, low, close, 14) / close

        vol_mean = vol.rolling(21).mean()
        vol_std  = vol.rolling(21).std()
        out["volume_zscore_21d"] = (vol - vol_mean) / (vol_std + 1e-9)

        out["parkinson_vol_10d"] = self._parkinson_vol(high, low, 10)

        out = out.dropna()
        return out

    def fit_normaliser(self, features: pd.DataFrame) -> None:
        """Fit the RobustScaler on training data."""
        self._scaler.fit(features[self.FEATURE_NAMES])
        self._scaler_fitted = True

    def normalise(self, features: pd.DataFrame) -> pd.DataFrame:
        """
        Apply the fitted RobustScaler.  Must call fit_normaliser() first
        (or fit_transform() for convenience).
        """
        if not self._scaler_fitted:
            raise RuntimeError("Call fit_normaliser() before normalise().")
        scaled = self._scaler.transform(features[self.FEATURE_NAMES])
        return pd.DataFrame(scaled, index=features.index, columns=self.FEATURE_NAMES)

    def fit_transform(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """Compute features AND fit+apply the scaler in one call (for training)."""
        raw = self.compute(ohlcv)
        self.fit_normaliser(raw)
        return self.normalise(raw)

    def transform(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """Compute features and apply a previously fitted scaler (for live/test)."""
        raw = self.compute(ohlcv)
        return self.normalise(raw)

    def get_feature_names(self) -> list[str]:
        return list(self.FEATURE_NAMES)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Lower-case column names and verify required columns exist."""
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"OHLCV DataFrame missing columns: {missing}")
        return df

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / (loss + 1e-9)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _atr(high: pd.Series, low: pd.Series,
             close: pd.Series, period: int) -> pd.Series:
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    @staticmethod
    def _parkinson_vol(high: pd.Series, low: pd.Series,
                       period: int) -> pd.Series:
        """Parkinson (1980) high-low range volatility estimator, annualised."""
        hl_ratio = np.log(high / low) ** 2
        factor   = 1.0 / (4.0 * np.log(2))
        return np.sqrt(factor * hl_ratio.rolling(period).mean() * 252)
