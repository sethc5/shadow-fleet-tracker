#!/usr/bin/env python3
"""Sync Shadow Fleet Tracker data to OSINTukraine archiving pipeline."""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("sync")

PROJECT_DIR = Path(__file__).parent.parent


def run_export(output: Path) -> bool:
    """Generate CSV export."""
    logger.info("Generating CSV export...")
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "export", "--output", str(output)],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Export failed: %s", result.stderr)
        return False
    logger.info("Export saved to %s", output)
    return True


def run_digest() -> Path | None:
    """Generate daily digest."""
    logger.info("Generating digest...")
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "digest"],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Digest failed: %s", result.stderr)
        return None

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    digest_path = PROJECT_DIR / "data" / "digests" / f"digest_{today}.md"
    if digest_path.exists():
        logger.info("Digest saved to %s", digest_path)
        return digest_path
    return None


def sync_to_osintukraine(csv_path: Path, digest_path: Path | None):
    """Send data to OSINTukraine archiving endpoint."""
    import os
    import requests
    from src.config import get_config

    cfg = get_config()
    api_url = cfg.get("osintukraine", {}).get("api_url", "")
    api_key = cfg.get("osintukraine", {}).get("api_key", "") or os.environ.get("OSINTUKRAINE_API_KEY", "")

    if not api_url:
        logger.info("OSINTukraine API URL not configured, skipping sync")
        return

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    # Upload CSV
    try:
        with open(csv_path, "rb") as f:
            resp = requests.post(
                f"{api_url}/upload/csv",
                files={"file": ("shadow_fleet_export.csv", f, "text/csv")},
                headers=headers,
                timeout=60,
            )
        if resp.status_code in (200, 201):
            logger.info("CSV uploaded to OSINTukraine")
        else:
            logger.warning("OSINTukraine upload returned %d: %s", resp.status_code, resp.text)
    except requests.RequestException as e:
        logger.error("OSINTukraine upload failed: %s", e)

    # Upload digest
    if digest_path and digest_path.exists():
        try:
            with open(digest_path, "rb") as f:
                resp = requests.post(
                    f"{api_url}/upload/digest",
                    files={"file": (digest_path.name, f, "text/markdown")},
                    headers=headers,
                    timeout=60,
                )
            if resp.status_code in (200, 201):
                logger.info("Digest uploaded to OSINTukraine")
            else:
                logger.warning("OSINTukraine digest upload returned %d", resp.status_code)
        except requests.RequestException as e:
            logger.error("OSINTukraine digest upload failed: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Sync to OSINTukraine archiving pipeline")
    parser.add_argument("--export-only", action="store_true", help="Only generate export, don't sync")
    parser.add_argument("--digest-only", action="store_true", help="Only generate digest, don't sync")
    args = parser.parse_args()

    csv_path = PROJECT_DIR / "data" / "export_osintukraine.csv"
    digest_path = None

    if not args.digest_only:
        if not run_export(csv_path):
            sys.exit(1)

    if not args.export_only:
        digest_path = run_digest()

    if not args.export_only and not args.digest_only:
        sync_to_osintukraine(csv_path, digest_path)

    logger.info("Done.")


if __name__ == "__main__":
    main()