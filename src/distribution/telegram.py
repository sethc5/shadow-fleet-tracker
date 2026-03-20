"""Telegram bot — digest distribution and interactive commands."""

import logging
import os
from pathlib import Path

from ..config import get_config
from ..db import Database

logger = logging.getLogger(__name__)


def _get_bot_token() -> str | None:
    """Get Telegram bot token from config or env."""
    cfg = get_config()
    return cfg.get("telegram", {}).get("bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")


def _get_chat_id() -> str | None:
    """Get default chat ID from config or env."""
    cfg = get_config()
    return cfg.get("telegram", {}).get("chat_id") or os.environ.get("TELEGRAM_CHAT_ID")


def _is_enabled() -> bool:
    """Check if Telegram is configured and enabled."""
    cfg = get_config()
    return cfg.get("telegram", {}).get("enabled", False) and bool(_get_bot_token())


def send_message(chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
    """Send a message to a Telegram chat.

    Returns True if successful.
    """
    token = _get_bot_token()
    if not token:
        logger.warning("Telegram bot token not configured")
        return False

    import requests

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }, timeout=30)

    if resp.status_code != 200:
        logger.error("Telegram send failed: %s", resp.text)
        return False

    logger.info("Sent message to Telegram chat %s", chat_id)
    return True


def send_digest(digest_path: Path | None = None, chat_id: str | None = None) -> bool:
    """Send today's digest to Telegram.

    Args:
        digest_path: Path to digest markdown file. If None, uses today's digest.
        chat_id: Telegram chat ID. If None, uses config default.
    """
    if not _is_enabled():
        logger.info("Telegram not enabled, skipping digest send")
        return False

    if digest_path is None:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        digest_path = Path("data") / "digests" / f"digest_{today}.md"

    if not digest_path.exists():
        logger.warning("Digest file not found: %s", digest_path)
        return False

    content = digest_path.read_text()

    # Telegram has a 4096 char limit — truncate if needed
    if len(content) > 4000:
        content = content[:3900] + "\n\n... (truncated, see full digest on GitHub Pages)"

    target = chat_id or _get_chat_id()
    if not target:
        logger.warning("No Telegram chat ID configured")
        return False

    return send_message(target, content)


def send_alert(imo: int, score: int, reasons: list[str], vessel_name: str = "", chat_id: str | None = None) -> bool:
    """Send a real-time alert notification to Telegram.

    Args:
        imo: Vessel IMO number
        score: Risk score
        reasons: List of risk reasons
        vessel_name: Vessel name
        chat_id: Override chat ID (uses alert_chat_id or chat_id from config)
    """
    if not _is_enabled():
        return False

    cfg = get_config()
    target = (
        chat_id
        or cfg.get("telegram", {}).get("alert_chat_id")
        or _get_chat_id()
    )

    if not target:
        return False

    sev = "🔴" if score >= 80 else "🟠" if score >= 60 else "🟡"
    name_str = f"*{vessel_name}*" if vessel_name else f"IMO {imo}"
    reasons_str = "\n".join(f"  • {r}" for r in reasons)

    text = (
        f"{sev} *Shadow Fleet Alert*\n\n"
        f"Vessel: {name_str}\n"
        f"IMO: `{imo}`\n"
        f"Score: *{score}/100*\n\n"
        f"Reasons:\n{reasons_str}"
    )

    return send_message(target, text)


def run_bot():
    """Start the Telegram bot in long-polling mode.

    Handles commands:
    /status — show DB stats
    /lookup <imo> — look up a vessel
    /track <imo> — fetch live AIS position
    /help — show available commands
    """
    token = _get_bot_token()
    if not token:
        logger.error("Telegram bot token not configured")
        return

    import requests
    from ..cli import cmd_lookup, cmd_track

    logger.info("Starting Telegram bot...")

    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            resp = requests.get(url, params={"offset": offset, "timeout": 30}, timeout=35)
            resp.raise_for_status()
            data = resp.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message", {})
                text = message.get("text", "").strip()
                chat_id = str(message.get("chat", {}).get("id", ""))

                if not text or not chat_id:
                    continue

                response = _handle_command(text)
                if response:
                    send_message(chat_id, response)

        except requests.RequestException as e:
            logger.warning("Bot polling error: %s", e)
        except KeyboardInterrupt:
            logger.info("Bot stopped")
            break


def _handle_command(text: str) -> str | None:
    """Handle a bot command and return response text."""
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    db = Database()

    if cmd == "/status":
        return (
            f"📊 *Shadow Fleet Tracker Status*\n\n"
            f"Vessels: {db.vessel_count()}\n"
            f"Sanctions: {db.sanctions_count()}\n"
            f"Alerts: {len(db.get_alerts(min_score=60))}"
        )

    elif cmd == "/lookup":
        if not arg or not arg.isdigit():
            return "Usage: /lookup <IMO number>"
        imo = int(arg)
        vessel = db.get_vessel(imo)
        if not vessel:
            return f"Vessel IMO {imo} not found in database."
        sanctions = db.get_sanctions_for_vessel(imo)
        return (
            f"🚢 *{vessel.name}*\n"
            f"IMO: `{vessel.imo}`\n"
            f"Flag: {vessel.flag or 'Unknown'}\n"
            f"Risk: {vessel.risk_score}/100\n"
            f"Sanctioned: {'Yes' if sanctions else 'No'}"
        )

    elif cmd == "/track":
        if not arg or not arg.isdigit():
            return "Usage: /track <IMO number>"
        return f"Tracking not available in bot mode. Use `sft track {arg}` in CLI."

    elif cmd == "/help":
        return (
            "*Shadow Fleet Tracker Bot*\n\n"
            "/status — Database stats\n"
            "/lookup <IMO> — Look up a vessel\n"
            "/track <IMO> — Track a vessel (CLI only)\n"
            "/help — This message"
        )

    return None