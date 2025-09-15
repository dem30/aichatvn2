# Build stage
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV SQLITE_DB_PATH=/tmp/app.db
ENV PORT=7860

RUN useradd -m -u 1000 appuser

# Cấp quyền cho các thư mục lưu trữ
RUN mkdir -p /tmp/nicegui /app/.nicegui /tmp/avatars /tmp/chat_files && \
    chown -R appuser:appuser /tmp/nicegui /app/.nicegui /tmp/avatars /tmp/chat_files && \
    chmod -R 777 /tmp/nicegui /app/.nicegui /tmp/avatars /tmp/chat_files

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY requirements.txt ./
COPY Dockerfile ./
COPY main.py app.py core2.py core.py config.py tabinterface.py ./
COPY uiapp/ uiapp/
COPY utils/ utils/

USER appuser
EXPOSE 7860
CMD ["uvicorn", "main:fastapi_app", "--host", "0.0.0.0", "--port", "7860"]