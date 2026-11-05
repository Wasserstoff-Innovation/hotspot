FROM python:3.11-alpine

WORKDIR /app

# Install minimal dependencies
RUN pip install --no-cache-dir starlette uvicorn

# Copy Hotspot module
COPY . /app/products/hotspot
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["python", "products/hotspot/standalone/server.py"]
