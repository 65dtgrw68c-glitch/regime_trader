"""Phase 3 slippage measurement: realized fill cost vs the assumed backtest rate.

`monitoring/logger.py::TradeLogger.log_fill()` has recorded `expected_price`
(the decision-time price passed through `submit_order()`) next to the actual
fill `price` since `46d2620` (2026-08-01). Before that commit the column was
never populated, so any FILL row logged earlier is not usable evidence.

This reads FILL rows from logs/trades.csv, converts price - expected_price
into a directional cost in bps (positive = the fill cost money relative to
the decision price, for both buys and sells), and compares the realized
distribution against config.BACKTEST["slippage"] — the flat 2bps assumption
every published Sharpe/CAGR number in this repo is built on.

    python scripts/slippage_check.py
    python scripts/slippage_check.py --path logs/trades.csv --min-fills 30

Requires live/paper fills logged after 2026-08-01; see
analysis_report_2026-08-01_deep_review.md, "Still open: Phase 3".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from settings import config  # noqa: E402

DEFAULT_PATH = Path(config.MONITORING["log_dir"]) / "trades.csv"


def load_fills(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"no trade log at {path}")
    df = pd.read_csv(path)
    df = df[df["event"] == "FILL"].copy()
    df = df[df["price"].notna() & df["expected_price"].notna()]
    df = df[(df["price"] != "") & (df["expected_price"] != "")]
    df["price"] = df["price"].astype(float)
    df["expected_price"] = df["expected_price"].astype(float)
    return df


def cost_bps(df: pd.DataFrame) -> pd.Series:
    """Directional cost: positive means the fill was worse than the decision
    price, for buys (paid more) and sells (received less) alike."""
    raw = (df["price"] - df["expected_price"]) / df["expected_price"] * 1e4
    sign = df["direction"].str.lower().map({"buy": 1.0, "sell": -1.0})
    if sign.isna().any():
        bad = sorted(df.loc[sign.isna(), "direction"].unique())
        raise SystemExit(f"unrecognized fill direction(s): {bad}")
    return raw * sign


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--min-fills", type=int, default=30,
                    help="fills needed before the estimate is treated as usable")
    a = ap.parse_args(argv)

    df = load_fills(a.path)
    assumed_bps = float(config.BACKTEST["slippage"]) * 1e4

    if df.empty:
        print(f"No FILL rows with both price and expected_price in {a.path}.")
        print("Either no fills have happened since the 2026-08-01 expected_price "
              "wiring (46d2620), or the deployed process isn't running.")
        print(f"Assumed cost in every published backtest number: {assumed_bps:.1f} bps.")
        return 1

    df["cost_bps"] = cost_bps(df)

    print(f"Source: {a.path}  ({len(df)} fills with usable price data)")
    print(f"Assumed backtest slippage: {assumed_bps:.1f} bps one-way\n")

    def report(label: str, g: pd.DataFrame) -> None:
        c = g["cost_bps"]
        print(f"{label:<10} n={len(c):<5} mean={c.mean():>7.2f}bps  "
              f"median={c.median():>7.2f}bps  std={c.std():>7.2f}bps  "
              f"min={c.min():>7.2f}  max={c.max():>7.2f}")

    report("ALL", df)
    for ticker, g in df.groupby("ticker"):
        report(ticker, g)

    if len(df) < a.min_fills:
        print(f"\nOnly {len(df)} fills — below --min-fills={a.min_fills}. "
              "Treat this as a preview, not a decision input; the Phase 3 "
              "verdict (2bps vs marketable-limit orders) needs more paper-"
              "trading days before it's trustworthy.")
        return 0

    measured = df["cost_bps"].mean()
    delta = measured - assumed_bps
    print(f"\nMeasured mean {measured:.2f}bps vs assumed {assumed_bps:.1f}bps "
          f"({delta:+.2f}bps).")
    if delta > assumed_bps:
        print("Realized cost is more than double the backtest assumption — "
              "revisit whether marketable-limit orders are worth it "
              "(see analysis_report_2026-08-01_deep_review.md Phase 3).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
