#!/usr/bin/env bash
# Build the venv on a 1 GB box: one package at a time, no wheel cache.
# Installing the whole requirements file at once makes pip's resolver hold
# every candidate in memory, which is what kills this machine.
set -e
cd /opt/whoop-loop

export PIP_NO_CACHE_DIR=1
export PIP_DISABLE_PIP_VERSION_CHECK=1

[ -d .venv ] || python3.11 -m venv .venv
PIP=./.venv/bin/pip

$PIP install --upgrade pip setuptools wheel

# Heaviest first, so their dependencies are already satisfied later on.
for pkg in \
  "numpy>=1.26.0" \
  "matplotlib>=3.9.0" \
  "pydantic>=2.9.0" \
  "pydantic-settings>=2.5.0" \
  "SQLAlchemy>=2.0.30" \
  "httpx>=0.27.0" \
  "aiogram>=3.7,<4" \
  "APScheduler>=3.10.4" \
  "fastapi>=0.115.0" \
  "uvicorn>=0.30.0" \
  "anthropic>=1.4.0" \
  "openai>=1.109.0" \
  "mcp>=2.0.0" \
  "python-dotenv>=1.0.0" \
  "tzdata>=2024.1"
do
  echo "=== ставлю $pkg ==="
  $PIP install "$pkg"
done

echo "=== проверка импортов ==="
./.venv/bin/python -c "import aiogram, matplotlib, sqlalchemy, anthropic, openai, mcp, apscheduler; print('все модули импортируются')"

echo "INSTALL_DONE"
