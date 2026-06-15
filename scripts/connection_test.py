"""
connection_test.py — Alpaca paper-account verification sequence (Prompt 6).

Run this AFTER filling in your .env credentials:

    python scripts/connection_test.py

It performs the six-step verification:
  1. Confirm the API connection is active and the account status is valid.
  2. Display current account equity (paper account ≈ $100,000).
  3. Check and display current market-hours status (open/closed).
  4. Place a test market BUY order for NVDA (1 share).
  5. Confirm the test order appears in the system.
  6. Cancel the test order after confirmation.

The market may be closed during testing — paper trading still queues the
order, which this script handles gracefully.

NOTE: This talks to the live Alpaca paper API and therefore CANNOT run in
CI or without valid credentials.  It is intentionally a standalone script,
not a pytest test.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broker.alpaca_client import AlpacaClient
from broker.order_executor import OrderExecutor
from broker.position_tracker import PositionTracker

TEST_TICKER = "NVDA"
TEST_QTY = 1


def main() -> int:
    print("=" * 60)
    print("ALPACA PAPER-ACCOUNT CONNECTION TEST")
    print("=" * 60)

    client = AlpacaClient()

    # ── Step 1: connection + account status ──────────────────────────────
    print("\n[1] Verifying connection and account status...")
    try:
        client.connect()
    except Exception as exc:
        print(f"    ✗ Could not connect: {exc}")
        print("    → Check that ALPACA_API_KEY / ALPACA_SECRET_KEY are set in .env")
        return 1

    if not client.verify_connection():
        print("    ✗ Connection verified but account is not ACTIVE.")
        return 1
    account = client.get_account()
    print(f"    ✓ Connected. Account status: {account['status']}")

    # ── Step 2: equity ───────────────────────────────────────────────────
    print("\n[2] Account equity:")
    print(f"    Equity:        ${account['equity']:,.2f}")
    print(f"    Buying power:  ${account['buying_power']:,.2f}")
    print(f"    Cash:          ${account['cash']:,.2f}")
    if abs(account["equity"] - 100_000) < 50_000:
        print("    ✓ Equity is in the expected paper range (~$100k).")

    # ── Step 3: market hours ─────────────────────────────────────────────
    print("\n[3] Market hours:")
    clock = client.get_clock()
    state = "OPEN" if clock["is_open"] else "CLOSED"
    print(f"    Market is currently: {state}")
    if not clock["is_open"]:
        print("    (Order will be queued for the next session — this is fine.)")

    # ── Step 4: place test BUY order ─────────────────────────────────────
    print(f"\n[4] Placing test market BUY: {TEST_QTY} share(s) of {TEST_TICKER}...")
    tracker = PositionTracker(client)
    executor = OrderExecutor(client, tracker)
    order_id = executor.submit_order(TEST_TICKER, TEST_QTY, "buy", order_type="market")
    if not order_id:
        print("    ✗ Order was rejected. See logs above.")
        return 1
    print(f"    ✓ Order submitted. ID: {order_id}")

    # ── Step 5: confirm order exists ─────────────────────────────────────
    print("\n[5] Confirming order is registered with Alpaca...")
    time.sleep(2)
    try:
        order = client.trading.get_order_by_id(order_id)
        print(f"    ✓ Order found. Status: {getattr(order, 'status', 'unknown')}")
        print("    → It should also be visible in the Alpaca paper dashboard.")
    except Exception as exc:
        print(f"    ✗ Could not fetch order: {exc}")

    # ── Step 6: cancel test order ────────────────────────────────────────
    print("\n[6] Cancelling test order...")
    try:
        executor.cancel_order(order_id)
        print("    ✓ Cancel request sent.")
    except Exception as exc:
        print(f"    ⚠ Cancel failed (order may already be filled): {exc}")

    print("\n" + "=" * 60)
    print("CONNECTION TEST COMPLETE")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
