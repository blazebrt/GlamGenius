FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Where the local media adapter writes in development. Overridden by a volume
# in docker-compose so uploads survive a container rebuild.
ENV MEDIA_LOCAL_ROOT=/data/media
RUN mkdir -p /data/media

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn server:app --host 0.0.0.0 --port 8000"]
