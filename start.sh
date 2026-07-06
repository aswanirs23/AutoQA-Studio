#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

source .venv/bin/activate

pip install -r requirements.txt
npm install
npm run build:css

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
