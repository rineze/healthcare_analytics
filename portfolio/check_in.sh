#!/usr/bin/env bash
# check_in.sh — Scheduled portfolio check-in (macOS/Linux equivalent of check_in.ps1).
#
# Pushes a Telegram nudge only when one is warranted. Silence is the expected
# and correct outcome most days.
#
# crontab -e, then:
#   0 8 * * *  /path/to/healthcare_analytics/portfolio/check_in.sh
set -euo pipefail
cd "$(dirname "$(dirname "$(realpath "$0")")")"
python3 portfolio/enrich.py --prices >/dev/null 2>&1 || true
python3 portfolio/gaps.py --notify
