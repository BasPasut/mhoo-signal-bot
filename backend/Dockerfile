FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /data is mounted as a Fly.io persistent volume.
# SQLite DB and ML models live here so they survive redeploys.
RUN mkdir -p /data/ml_models /data/ml_history

EXPOSE 8000

# DATABASE_URL and ML paths point to /data — override via fly secrets if needed
ENV DATABASE_URL="sqlite:////data/signalbot.db"
ENV ML_MODELS_DIR="/data/ml_models"
ENV ML_HISTORY_DIR="/data/ml_history"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
