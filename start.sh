#!/bin/bash
set -e

echo "==> Initializing database schema..."
python main.py init-db

if [ "${RUN_MODE}" = "dashboard" ]; then
  echo "==> Dashboard mode: starting Streamlit on port ${PORT:-8501}..."
  exec streamlit run src/dashboard/app.py \
      --server.port="${PORT:-8501}" \
      --server.address=0.0.0.0 \
      --server.headless=true \
      --server.enableCORS=false \
      --server.enableXsrfProtection=false
else
  echo "==> API mode: starting FastAPI on port ${PORT:-8000}..."
  uvicorn src.api.main:app --host 0.0.0.0 --port "${PORT:-8000}" &

  echo "==> Starting Streamlit on port 8501..."
  exec streamlit run src/dashboard/app.py \
      --server.port=8501 \
      --server.address=0.0.0.0 \
      --server.headless=true \
      --server.enableCORS=false \
      --server.enableXsrfProtection=false
fi
