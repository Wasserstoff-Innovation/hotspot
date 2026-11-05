FROM python:3.12-alpine

WORKDIR /app

# Build context is the repository root (see docker-compose.yml), so only the product is copied.
COPY products/hotspot/standalone/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY products/hotspot /app/products/hotspot

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

CMD ["python", "products/hotspot/standalone/server.py"]
