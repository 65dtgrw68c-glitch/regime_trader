"""Operator tool to review and safely clear a fired RISK_HALT breaker.

The max-drawdown circuit breaker (`core/risk_manager.py::RiskManager`) is a
manual-reset one-way door by design: once it fires, the bot stops trading
and stays stopped until a human reviews the incident and clears it. That is
intentional (see deploy/ANLEITUNG.md, section 9) — the only thing this tool
changes is HOW the clear happens, not WHETHER a human decides it.

Before this tool, clearing a halt meant SSH-ing in and running two blind
`sudo rm` commands against files the operator has to remember to delete
BOTH of, with no display of what actually happened and no re-anchoring of
the peak-equity baseline until the NEXT scheduled run picks up the missing
state file (see RiskManager.clear_lock()'s docstring for why the re-anchor
matters: leaving the old peak in place re-triggers the breaker on the very
next equity update).

This tool instead:
  1. Prints the full halt incident (the lock file's JSON) for review.
  2. Fetches the account's REAL current equity from Alpaca (or accepts
     --current-equity to skip that lookup).
  3. Shows exactly what the peak-equity baseline will be re-anchored to.
  4. Requires an explicit typed confirmation (or --yes) before touching
     anything.
  5. Calls the existing, already-correct RiskManager.clear_lock(), which
     deletes the lock file and immediately rewrites the state file with the
     re-anchored peak (rather than the manual procedure's two-file delete,
     which leaves the re-anchor to whatever equity the next scheduled run
     happens to observe).

It does not decide whether resuming is a good idea. It does not loosen the
35% threshold or run without a human confirming. It only removes the SSH
foot-guns (forgetting a file, not seeing the incident, an uncertain
re-anchor delay) from a step a human was always going to do anyway.

    python scripts/clear_halt.py                              # review + confirm interactively
    python scripts/clear_halt.py --yes                         # skip the prompt (after reviewing a runbook)
    python scripts/clear_halt.py --current-equity 71234.56     # skip the broker lookup
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.risk_manager import RiskManager  # noqa: E402
from settings import config  # noqa: E402


def _fetch_current_equity() -> float:
    from broker.alpaca_client import AlpacaClient
    client = AlpacaClient()
    client.connect()
    return float(client.get_account()["equity"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lock-path", default=config.RISK["lock_file_path"])
    ap.add_argument("--current-equity", type=float, default=None,
                     help="skip the Alpaca account lookup and re-anchor the "
                          "peak to this value instead")
    ap.add_argument("--yes", action="store_true",
                     help="skip the interactive confirmation prompt")
    a = ap.parse_args(argv)

    rm = RiskManager(lock_file_path=a.lock_path)
    if not rm.is_halted():
        print(f"{a.lock_path} does not exist — the bot is not halted. Nothing to do.")
        return 0

    payload = json.loads(rm.lock_path.read_text())
    print("=" * 72)
    print("RISK HALT is ACTIVE. Incident report:")
    print("=" * 72)
    print(json.dumps(payload, indent=2))
    print("=" * 72)

    if a.current_equity is not None:
        current_equity = a.current_equity
        print(f"\nUsing --current-equity=${current_equity:,.2f} (no broker lookup).")
    else:
        print("\nFetching current account equity from Alpaca ...")
        try:
            current_equity = _fetch_current_equity()
        except Exception as exc:
            print(f"Could not fetch account equity: {exc}")
            print("Re-run with --current-equity <value> if you want to "
                  "proceed without a live broker connection.")
            return 1
        print(f"Current equity: ${current_equity:,.2f}")

    old_peak = rm.state().peak_equity
    threshold = config.RISK["cb_max_drawdown_halt"] * 100
    print(
        f"\nClearing this halt will:\n"
        f"  - delete {rm.lock_path}\n"
        f"  - rewrite {rm.state_path} with a fresh peak equity\n"
        f"  - re-anchor the drawdown breaker's peak equity: "
        f"${old_peak:,.2f} -> ${current_equity:,.2f}\n"
        f"  - allow trading to resume on the NEXT scheduled run\n\n"
        f"This does not undo any losses and does not change the "
        f"{threshold:.0f}% halt threshold. Only proceed after reviewing "
        f"the incident above."
    )

    if not a.yes:
        answer = input("\nType 'yes' to clear the halt and resume trading: ").strip().lower()
        if answer != "yes":
            print("Aborted — halt left in place.")
            return 1

    # Populate _current_equity with the REAL fetched value so clear_lock()
    # re-anchors the peak to it immediately, rather than leaving that to
    # whatever equity the next scheduled run happens to observe.
    rm.update_equity(current_equity)
    cleared = rm.clear_lock()
    if cleared:
        print(f"\nHalt cleared. Peak equity re-anchored to ${current_equity:,.2f}. "
              f"Trading will resume on the next scheduled run.")
        return 0
    print("\nclear_lock() reported nothing to clear (unexpected — the lock "
          "file may have been removed by another process concurrently).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
