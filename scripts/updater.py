#!/usr/bin/env python3
"""Shadow Fleet Tracker — hourly update daemon.

Runs a cycle every hour:
1. Ingest sanctions data from all sources
2. Discover new vessels
3. Track positions for top vessels
4. Run scoring
5. Regenerate site dashboard
6. Publish to GitHub Pages

Usage:
    python scripts/updater.py              # Run hourly loop
    python scripts/updater.py --once       # Run one cycle and exit
    python scripts/updater.py --interval 1800  # Every 30 minutes
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/updater.log", mode="a"),
    ],
)
logger = logging.getLogger("updater")

PROJECT_DIR = Path(__file__).parent.parent
PID_FILE = Path("data") / "updater.pid"
PYTHON = sys.executable


def write_pid():
    """Write PID file."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def remove_pid():
    """Remove PID file."""
    if PID_FILE.exists():
        PID_FILE.unlink()


def run_cmd(cmd: list[str], desc: str, retries: int = 3, delay: int = 5) -> bool:
    """Run a CLI command and log the result.
    
    Args:
        cmd: Command to run as list of strings
        desc: Human-readable description for logging
        retries: Number of retry attempts on failure
        delay: Delay between retries in seconds
        
    Returns:
        True if command succeeded, False otherwise
    """
    attempt = 0
    while attempt <= retries:
        logger.info("Running: %s (attempt %d/%d)", desc, attempt + 1, retries + 1)
        try:
            result = subprocess.run(
                [PYTHON, "-m"] + cmd,
                cwd=str(PROJECT_DIR),
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout per step
            )
            if result.returncode != 0:
                error_msg = result.stderr[:500] if result.stderr else "Unknown error"
                logger.error("%s failed: %s", desc, error_msg)
                attempt += 1
                if attempt <= retries:
                    logger.info("Retrying in %d seconds...", delay)
                    time.sleep(delay)
                    continue
                return False
            
            if result.stdout.strip():
                logger.info("%s: %s", desc, result.stdout.strip()[-200:])
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("%s timed out", desc)
            attempt += 1
            if attempt <= retries:
                logger.info("Retrying in %d seconds...", delay)
                time.sleep(delay)
                continue
            return False
        except Exception as e:
            logger.error("%s error: %s", desc, e)
            attempt += 1
            if attempt <= retries:
                logger.info("Retrying in %d seconds...", delay)
                time.sleep(delay)
                continue
            return False
    
    return False


def run_cycle():
    """Run one update cycle."""
    start = datetime.now()
    logger.info("=== Update cycle started at %s ===", start.isoformat())

    steps = [
        (["src.cli", "ingest", "--source", "all"], "Ingest sanctions"),
        (["src.cli", "ingest", "--source", "eu"], "Ingest EU sanctions"),
        (["src.cli", "unpack-sdn", "--output", "docs/ofac_sdn_vessels.json"], "Unpack OFAC SDN JSON"),
    ]

    success_count = 0
    for cmd, desc in steps:
        if run_cmd(cmd, desc):
            success_count += 1

    # Discover new vessels
    try:
        sys.path.insert(0, str(PROJECT_DIR))
        from src.db import Database
        from src.ingest.ais import discover_new_vessels
        db = Database()
        new = discover_new_vessels(db)
        logger.info("Discover: %d new vessels found", new)
    except Exception as e:
        logger.error("Discover failed: %s", e)

    # Score
    run_cmd(["src.cli", "score"], "Run scoring")

    # Track top vessels
    run_cmd(["src.cli", "track-all", "--limit", "30"], "Track top 30 vessels")

    # Generate site
    run_cmd(["src.cli", "site"], "Generate site dashboard")

    # Publish to git
    try:
        subprocess.run(
            ["git", "add", "docs/"],
            cwd=str(PROJECT_DIR),
            capture_output=True,
        )
        date_str = start.strftime("%Y-%m-%d %H:%M")
        subprocess.run(
            ["git", "commit", "-m", f"auto-update: {date_str}", "--allow-empty"],
            cwd=str(PROJECT_DIR),
            capture_output=True,
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            timeout=120,
        )
        logger.info("Published to GitHub")
    except Exception as e:
        logger.warning("Git publish failed: %s", e)

    elapsed = (datetime.now() - start).total_seconds()
    logger.info("=== Cycle complete in %.0fs (%d/%d steps succeeded) ===\n", elapsed, success_count, len(steps))


def main():
    parser = argparse.ArgumentParser(description="Shadow Fleet Tracker updater daemon")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between cycles (default: 3600)")
    args = parser.parse_args()

    # Ensure data dir exists
    Path("data").mkdir(exist_ok=True)

    # Check if already running
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)  # Check if process exists
            logger.error("Updater already running (PID %d)", old_pid)
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            # Stale PID file
            PID_FILE.unlink()

    write_pid()
    shutdown = False

    def handle_signal(signum, frame):
        nonlocal shutdown
        logger.info("Received signal %d, shutting down", signum)
        shutdown = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        if args.once:
            run_cycle()
        else:
            logger.info("Starting hourly updater (interval: %ds)", args.interval)
            while not shutdown:
                run_cycle()
                # Sleep in small increments to respond to signals quickly
                for _ in range(args.interval):
                    if shutdown:
                        break
                    time.sleep(1)
    finally:
        remove_pid()
        logger.info("Updater stopped")


if __name__ == "__main__":
    main()
