"""
Alerts — email and webhook notifications for critical events (Prompt 8).

Critical events with dedicated triggers:
  * Any circuit breaker triggered
  * Daily drawdown exceeds the configured threshold
  * Bot stopped due to the 10% drawdown lock file
  * Alpaca API connection lost

Channels: email (SMTP) and webhook (Slack/Discord).  All thresholds and
channel toggles live in settings/config.ALERTS.  Duplicate alerts for the
same key are throttled within a cooldown window.

`requests` and `smtplib` are imported lazily so the module is import-safe and
unit-testable without network access.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from settings import config

logger = logging.getLogger(__name__)


# Severity levels routed to different channels.
SEVERITY_INFO     = "INFO"
SEVERITY_WARNING  = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"

# Default cooldown (seconds) before the same alert key may fire again.
_DEFAULT_COOLDOWN = config.ALERTS.get("cooldown_seconds", 300)


class AlertManager:
    """Unified alerting facade across email + webhook channels."""

    def __init__(
        self,
        email_recipients: Optional[list[str]] = None,
        webhook_url: Optional[str] = None,
        cooldown_seconds: int = _DEFAULT_COOLDOWN,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        sender: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        alerts_cfg = config.ALERTS
        self._recipients = (
            email_recipients if email_recipients is not None
            else config.MONITORING["alert_email_recipients"]
        )
        self._webhook_url = (
            webhook_url if webhook_url is not None
            else config.MONITORING["alert_webhook_url"]
        )
        self._cooldown = cooldown_seconds
        self._smtp_host = smtp_host or alerts_cfg["smtp_host"]
        self._smtp_port = smtp_port or alerts_cfg["smtp_port"]
        self._sender = sender or alerts_cfg["email_sender"]
        self._enabled = alerts_cfg["enabled"] if enabled is None else enabled
        self._email_enabled = alerts_cfg["email_enabled"]
        self._webhook_enabled = alerts_cfg["webhook_enabled"]
        # key -> last sent monotonic timestamp
        self._last_sent: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Generic dispatch
    # ------------------------------------------------------------------

    def alert(
        self,
        message: str,
        severity: str = SEVERITY_WARNING,
        context: Optional[dict] = None,
        key: Optional[str] = None,
    ) -> bool:
        """
        Dispatch an alert.  Returns True if it was sent, False if throttled
        or disabled.  `key` identifies the alert condition for throttling.
        """
        if not self._enabled:
            return False
        throttle_key = key or message
        if self._is_throttled(throttle_key):
            logger.debug("Alert throttled: %s", throttle_key)
            return False

        context = context or {}
        subject = f"[{severity}] regime_trader: {message}"
        body = self._format_body(message, severity, context)

        # Route by severity: CRITICAL/WARNING → email; everything → webhook.
        if severity in (SEVERITY_CRITICAL, SEVERITY_WARNING) and self._recipients:
            self._send_email(subject, body)
        if self._webhook_url:
            self._send_webhook({"text": subject, "severity": severity, **context})

        self._last_sent[throttle_key] = time.monotonic()
        logger.info("Alert sent [%s]: %s", severity, message)
        return True

    # ------------------------------------------------------------------
    # Dedicated triggers for the four critical events
    # ------------------------------------------------------------------

    def alert_circuit_breaker(self, level_name: str, context: Optional[dict] = None) -> bool:
        return self.alert(
            f"Circuit breaker triggered: {level_name}",
            SEVERITY_CRITICAL, context, key=f"cb_{level_name}",
        )

    def alert_daily_drawdown(self, drawdown: float,
                             threshold: Optional[float] = None) -> bool:
        threshold = threshold if threshold is not None else config.ALERTS["daily_drawdown_alert"]
        return self.alert(
            f"Daily drawdown {drawdown*100:.2f}% exceeded threshold "
            f"{threshold*100:.2f}%.",
            SEVERITY_WARNING, {"drawdown": drawdown, "threshold": threshold},
            key="daily_drawdown",
        )

    def alert_lock_file(self, context: Optional[dict] = None) -> bool:
        return self.alert(
            "Bot halted: 10% drawdown lock file written. Manual review required.",
            SEVERITY_CRITICAL, context, key="lock_file",
        )

    def alert_api_connection_lost(self, context: Optional[dict] = None) -> bool:
        return self.alert(
            "Alpaca API connection lost.",
            SEVERITY_CRITICAL, context, key="api_connection_lost",
        )

    # ------------------------------------------------------------------
    # Threshold helper
    # ------------------------------------------------------------------

    @staticmethod
    def should_alert_drawdown(drawdown: float, threshold: Optional[float] = None) -> bool:
        """True when |drawdown| meets/exceeds the configured alert threshold."""
        threshold = threshold if threshold is not None else config.ALERTS["daily_drawdown_alert"]
        return drawdown <= -abs(threshold)

    # ------------------------------------------------------------------
    # Throttling
    # ------------------------------------------------------------------

    def _is_throttled(self, key: str) -> bool:
        last = self._last_sent.get(key)
        if last is None:
            return False
        return (time.monotonic() - last) < self._cooldown

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    def _send_email(self, subject: str, body: str) -> None:
        """Send an email via SMTP.  Failures are logged, never raised."""
        try:
            import smtplib
            from email.mime.text import MIMEText

            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = self._sender
            msg["To"] = ", ".join(self._recipients)

            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as server:
                if config.ALERTS.get("smtp_use_tls"):
                    server.starttls()
                server.sendmail(self._sender, self._recipients, msg.as_string())
        except Exception as exc:                # pragma: no cover - network
            logger.error("Failed to send email alert: %s", exc)

    def _send_webhook(self, payload: dict) -> None:
        """POST to the configured webhook.  Failures are logged, never raised."""
        try:
            import requests
            requests.post(self._webhook_url, json=payload, timeout=10)
        except Exception as exc:                # pragma: no cover - network
            logger.error("Failed to send webhook alert: %s", exc)

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_body(message: str, severity: str, context: dict) -> str:
        lines = [f"Severity: {severity}", f"Message:  {message}", ""]
        if context:
            lines.append("Context:")
            for k, v in context.items():
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)
