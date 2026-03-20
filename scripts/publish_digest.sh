#!/bin/bash
# Generate full site and publish to GitHub Pages
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=== Generating digest ==="
.venv/bin/python -m src.cli digest

echo "=== Generating full site ==="
.venv/bin/python -m src.cli site

TODAY=$(date +%Y-%m-%d)
DIGEST_FILE="data/digests/digest_${TODAY}.md"

# Copy digest to docs/archive
mkdir -p docs/archive
if [ -f "$DIGEST_FILE" ]; then
    cp "$DIGEST_FILE" "docs/archive/"
fi

# Generate Jekyll config if needed
if [ ! -f "docs/_config.yml" ]; then
    cat > "docs/_config.yml" << 'EOF'
theme: jekyll-theme-minimal
title: Shadow Fleet Tracker
description: Daily monitoring of sanctioned Russian oil tankers
baseurl: ""
url: ""
EOF
fi

echo "=== Publishing to GitHub Pages ==="
git add docs/
git commit -m "site: ${TODAY}" --allow-empty
git push origin main

echo "Done. Site published to GitHub Pages."