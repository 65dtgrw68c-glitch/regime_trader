"""
HMM Engine — market regime detection via Gaussian Hidden Markov Model.

Design decisions
----------------
* Automatic regime count selection (3–7) via BIC over the training window.
* Regimes are re-labelled after every fit by ascending mean daily return so
  they carry a consistent economic meaning regardless of the raw HMM state
  numbering produced by EM.
* Look-ahead prevention: we never call hmmlearn's .predict() / .decode()
  on future data.  Live inference uses a pure forward-pass (alpha recursion)
  so that at bar t only observations [0..t] are consumed.
* Stability filter: a candidate regime must hold for >=3 consecutive bars
  before it becomes the "confirmed" regime.  If the raw signal flickers >4
  times in the last 20 bars a high-uncertainty flag is set.
"""

from __future__ import annotations

import logging
import pickle
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Label maps by n_components
# ---------------------------------------------------------------------------
_LABEL_MAPS: dict[int, list[str]] = {
    3: ["Bear",    "Neutral", "Bull"],
    4: ["Crash",   "Bear",    "Bull",    "Euphoria"],
    5: ["Crash",   "Bear",    "Neutral", "Bull", "Euphoria"],
    6: ["Crash",   "Bear",    "Weak",    "Neutral", "Bull", "Euphoria"],
    7: ["Crash",   "Deep Bear","Bear",   "Neutral", "Bull", "Strong Bull", "Euphoria"],
}

# ---------------------------------------------------------------------------
# BIC selection range
# ---------------------------------------------------------------------------
_MIN_COMPONENTS = 3
_MAX_COMPONENTS = 7

# Stability filter constants
_CONFIRM_BARS  = 3   # raw regime must persist this many bars to be confirmed
_FLICKER_WINDOW = 20  # look-back window for flicker detection
_FLICKER_LIMIT  = 4   # max transitions in _FLICKER_WINDOW before warning


class HMMEngine:
    """
    Gaussian HMM regime detector with automatic state-count selection,
    economic regime labelling, forward-only inference, and a stability filter.
    """

    def __init__(
        self,
        covariance_type: str = "full",
        n_iter: int = 100,
        random_state: int = 42,
        min_history_bars: int = 252,
        refit_interval_bars: int = 21,
    ) -> None:
        self.covariance_type   = covariance_type
        self.n_iter            = n_iter
        self.random_state      = random_state
        self.min_history_bars  = min_history_bars
        self.refit_interval_bars = refit_interval_bars

        # Set after .fit()
        self._model:           Optional[GaussianHMM] = None
        self._n_components:    int = 0
        self._regime_labels:   list[str] = []
        # Map internal HMM state index -> sorted-by-return rank index
        self._state_to_label:  dict[int, int] = {}
        # Reverse: sorted rank -> HMM state
        self._label_to_state:  dict[int, int] = {}

        # Forward algorithm state (updated bar by bar in live mode)
        self._log_alpha: Optional[np.ndarray] = None  # shape (n_components,)

        # Stability filter state
        self._raw_history:    deque[int] = deque(maxlen=_FLICKER_WINDOW)
        self._candidate_regime: Optional[int] = None
        self._candidate_count:  int = 0
        self._confirmed_regime: Optional[int] = None
        self.high_uncertainty:  bool = False

        # Bar counter for refit scheduling
        self._bars_since_fit: int = 0

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def fit(self, features: pd.DataFrame) -> None:
        """
        Select optimal n_components via BIC, fit the GaussianHMM, and
        re-label regimes by ascending mean return.

        Parameters
        ----------
        features : pd.DataFrame
            Normalised feature matrix from FeatureEngineer (no NaNs).
        """
        X = features.values.astype(float)
        if len(X) < self.min_history_bars:
            raise ValueError(
                f"Need at least {self.min_history_bars} bars to fit, "
                f"got {len(X)}."
            )

        best_model, best_bic, best_k = None, np.inf, _MIN_COMPONENTS
        for k in range(_MIN_COMPONENTS, _MAX_COMPONENTS + 1):
            model = self._build_model(k)
            try:
                model.fit(X)
            except Exception as exc:
                logger.debug("HMM fit failed for k=%d: %s", k, exc)
                continue
            if not model.monitor_.converged:
                logger.debug("HMM did not converge for k=%d", k)
            bic = self._bic(model, X)
            logger.debug("k=%d  BIC=%.2f  converged=%s", k, bic, model.monitor_.converged)
            if bic < best_bic:
                best_bic, best_model, best_k = bic, model, k

        if best_model is None:
            raise RuntimeError("HMM failed to fit for every candidate k.")

        self._model       = best_model
        self._n_components = best_k
        logger.info("HMM selected k=%d  BIC=%.2f", best_k, best_bic)

        self._build_label_map(features)
        self._reset_forward_state()
        self._bars_since_fit = 0

    # ------------------------------------------------------------------
    # Live / forward-only inference
    # ------------------------------------------------------------------

    def update(self, observation: np.ndarray) -> int:
        """
        Consume one new observation vector (shape (n_features,)) using the
        forward algorithm and return the confirmed regime label index.

        This is the ONLY method that should be called bar-by-bar in live
        trading.  It never sees future data.

        Returns
        -------
        int
            Index into self.regime_labels for the currently confirmed regime,
            or -1 if fewer than _CONFIRM_BARS bars have been processed.
        """
        self._require_fitted()
        obs = np.asarray(observation, dtype=float).reshape(1, -1)
        self._log_alpha = self._forward_step(self._log_alpha, obs)
        raw_state = int(np.argmax(self._log_alpha))
        ranked    = self._state_to_label[raw_state]
        self._update_stability_filter(ranked)
        self._bars_since_fit += 1
        return self._confirmed_regime if self._confirmed_regime is not None else -1

    def update_batch(self, features: pd.DataFrame) -> np.ndarray:
        """
        Run update() on each row in sequence (oldest → newest).
        Returns an integer array of confirmed regime indices,
        shape (len(features),).  Values are -1 until the stability filter
        has collected enough bars.

        This is the look-ahead-free batch path used in backtesting.
        """
        self._require_fitted()
        results = np.full(len(features), -1, dtype=int)
        X = features.values.astype(float)
        for i, row in enumerate(X):
            results[i] = self.update(row)
        return results

    def predict_proba_current(self) -> np.ndarray:
        """
        Return the posterior probability vector over ranked regimes for the
        most recently consumed observation.  Shape: (n_components,).
        """
        self._require_fitted()
        if self._log_alpha is None:
            return np.full(self._n_components, 1.0 / self._n_components)
        log_alpha = self._log_alpha
        log_sum   = np.logaddexp.reduce(log_alpha)
        proba_raw = np.exp(log_alpha - log_sum)
        # Re-order from HMM-state order to ranked order
        proba_ranked = np.zeros(self._n_components)
        for hmm_state, rank in self._state_to_label.items():
            proba_ranked[rank] = proba_raw[hmm_state]
        return proba_ranked

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def current_regime(self) -> int:
        """Confirmed regime index (ranked by return), or -1 if not yet stable."""
        return self._confirmed_regime if self._confirmed_regime is not None else -1

    def current_regime_label(self) -> str:
        """Human-readable label for the confirmed regime."""
        idx = self.current_regime()
        if idx < 0:
            return "Unknown"
        return self._regime_labels[idx]

    @property
    def regime_labels(self) -> list[str]:
        return list(self._regime_labels)

    @property
    def n_components(self) -> int:
        return self._n_components

    def should_refit(self, bar_index: int) -> bool:
        """True when bar_index is an exact multiple of refit_interval_bars."""
        return bar_index > 0 and bar_index % self.refit_interval_bars == 0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Pickle the entire engine (model + metadata) to disk."""
        payload = {
            "model":            self._model,
            "n_components":     self._n_components,
            "regime_labels":    self._regime_labels,
            "state_to_label":   self._state_to_label,
            "label_to_state":   self._label_to_state,
            "covariance_type":  self.covariance_type,
            "n_iter":           self.n_iter,
            "random_state":     self.random_state,
            "min_history_bars": self.min_history_bars,
            "refit_interval_bars": self.refit_interval_bars,
        }
        Path(path).write_bytes(pickle.dumps(payload))
        logger.info("HMMEngine saved to %s", path)

    @classmethod
    def load(cls, path: str) -> "HMMEngine":
        """Restore an engine from disk."""
        payload = pickle.loads(Path(path).read_bytes())
        engine = cls(
            covariance_type     = payload["covariance_type"],
            n_iter              = payload["n_iter"],
            random_state        = payload["random_state"],
            min_history_bars    = payload["min_history_bars"],
            refit_interval_bars = payload["refit_interval_bars"],
        )
        engine._model           = payload["model"]
        engine._n_components    = payload["n_components"]
        engine._regime_labels   = payload["regime_labels"]
        engine._state_to_label  = payload["state_to_label"]
        engine._label_to_state  = payload["label_to_state"]
        engine._reset_forward_state()
        logger.info("HMMEngine loaded from %s", path)
        return engine

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_model(self, k: int) -> GaussianHMM:
        return GaussianHMM(
            n_components    = k,
            covariance_type = self.covariance_type,
            n_iter          = self.n_iter,
            random_state    = self.random_state,
            verbose         = False,
        )

    @staticmethod
    def _bic(model: GaussianHMM, X: np.ndarray) -> float:
        """
        Bayesian Information Criterion.
        Lower is better.  n_params for a Gaussian HMM with full covariance:
          transition matrix:  k*(k-1)
          means:              k * d
          covariances:        k * d*(d+1)/2   (full)
        """
        k = model.n_components
        d = X.shape[1]
        n_params = k * (k - 1) + k * d + k * d * (d + 1) // 2
        log_likelihood = model.score(X) * len(X)
        return -2 * log_likelihood + n_params * np.log(len(X))

    def _build_label_map(self, features: pd.DataFrame) -> None:
        """
        Assign a stable economic meaning to each HMM state by sorting
        states on their mean 1-day return contribution.

        The 'ret_1d' feature is the first column in FEATURE_NAMES; if the
        feature matrix doesn't contain it we fall back to the first column.
        """
        X = features.values.astype(float)
        raw_states = self._model.predict(X)  # safe: only used internally for sorting

        ret_col = 0
        if hasattr(features, "columns"):
            cols = list(features.columns)
            if "ret_1d" in cols:
                ret_col = cols.index("ret_1d")

        mean_returns = np.array([
            X[raw_states == s, ret_col].mean() if (raw_states == s).any() else 0.0
            for s in range(self._n_components)
        ])

        # rank_order[i] = HMM state index that has the i-th lowest mean return
        rank_order = np.argsort(mean_returns)

        self._state_to_label = {int(hmm_s): int(rank) for rank, hmm_s in enumerate(rank_order)}
        self._label_to_state = {int(rank): int(hmm_s) for rank, hmm_s in enumerate(rank_order)}

        label_map = _LABEL_MAPS.get(self._n_components)
        if label_map is None:
            label_map = [f"Regime_{i}" for i in range(self._n_components)]
        self._regime_labels = label_map

        logger.info(
            "Regime labels: %s",
            {self._regime_labels[r]: f"mean_ret={mean_returns[s]:.4f}"
             for r, s in self._label_to_state.items()},
        )

    # ------------------------------------------------------------------
    # Forward algorithm (no look-ahead)
    # ------------------------------------------------------------------

    def _reset_forward_state(self) -> None:
        """Reset the incremental forward pass and stability filter."""
        self._log_alpha = None
        self._raw_history.clear()
        self._candidate_regime = None
        self._candidate_count  = 0
        self._confirmed_regime = None
        self.high_uncertainty  = False

    def _forward_step(
        self,
        prev_log_alpha: Optional[np.ndarray],
        obs: np.ndarray,
    ) -> np.ndarray:
        """
        One step of the forward (alpha) recursion in log-space.

        alpha_t(j) = p(o_t | state=j) * sum_i [ alpha_{t-1}(i) * a(i,j) ]

        Parameters
        ----------
        prev_log_alpha : (n_components,) or None
            Log-alpha from the previous bar.  None on the first bar.
        obs : (1, n_features)
            The current bar's observation vector.

        Returns
        -------
        (n_components,) log-alpha for the current bar.
        """
        model = self._model
        k     = model.n_components
        log_transmat = np.log(model.transmat_ + 1e-300)

        # Emission log-probability for each state given obs
        log_emit = model._compute_log_likelihood(obs)[0]  # shape (k,)

        if prev_log_alpha is None:
            # First observation: use initial state distribution
            log_init     = np.log(model.startprob_ + 1e-300)
            log_alpha    = log_init + log_emit
        else:
            # log sum_i alpha_{t-1}(i) * a(i,j)  — computed in log-space
            # log_transmat shape: (k, k)  where [i, j] = log P(j | i)
            log_alpha_prev_col = prev_log_alpha[:, np.newaxis]  # (k, 1)
            log_pred = np.logaddexp.reduce(log_alpha_prev_col + log_transmat, axis=0)
            log_alpha = log_pred + log_emit

        return log_alpha

    # ------------------------------------------------------------------
    # Stability filter
    # ------------------------------------------------------------------

    def _update_stability_filter(self, raw_ranked: int) -> None:
        """
        Enforce the 3-bar confirmation rule and detect flickering.

        raw_ranked : the ranked regime index from the most recent forward step.
        """
        self._raw_history.append(raw_ranked)

        # --- 3-bar persistence ---
        if raw_ranked == self._candidate_regime:
            self._candidate_count += 1
        else:
            if self._candidate_regime is not None:
                logger.debug(
                    "Regime candidate changed: %s -> %s (confirmed was %s)",
                    self._regime_label_str(self._candidate_regime),
                    self._regime_label_str(raw_ranked),
                    self._regime_label_str(self._confirmed_regime),
                )
            self._candidate_regime = raw_ranked
            self._candidate_count  = 1

        if self._candidate_count >= _CONFIRM_BARS:
            old = self._confirmed_regime
            if old != raw_ranked:
                logger.warning(
                    "REGIME CHANGE: %s -> %s",
                    self._regime_label_str(old),
                    self._regime_label_str(raw_ranked),
                )
                self._confirmed_regime = raw_ranked

        # --- Flicker detection ---
        transitions = sum(
            1
            for a, b in zip(list(self._raw_history)[:-1], list(self._raw_history)[1:])
            if a != b
        )
        if transitions > _FLICKER_LIMIT:
            if not self.high_uncertainty:
                logger.warning(
                    "HIGH UNCERTAINTY: %d regime transitions in last %d bars.",
                    transitions, len(self._raw_history),
                )
            self.high_uncertainty = True
        else:
            self.high_uncertainty = False

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _require_fitted(self) -> None:
        if self._model is None:
            raise RuntimeError("HMMEngine has not been fitted.  Call .fit() first.")

    def _regime_label_str(self, idx: Optional[int]) -> str:
        if idx is None or idx < 0:
            return "Unknown"
        try:
            return self._regime_labels[idx]
        except IndexError:
            return f"Regime_{idx}"
