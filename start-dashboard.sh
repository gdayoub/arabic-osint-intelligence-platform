#!/bin/bash
set -e

echo "==> Initializing database schema..."
python main.py init-db

echo "==> Starting Streamlit dashboard on port ${PORT:-8501}..."
exec streamlit run src/dashboard/app.py \
    --server.port="${PORT:-8501}" \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
