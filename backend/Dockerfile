FROM python:3.12-slim

WORKDIR /app

ENV PYTHONPATH=/app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Create storage directories
RUN mkdir -p /var/lib/qemu-interface/{images,cloud-init,storage}

# Download API docs assets (self-hosted, no CDN dependency)
RUN mkdir -p /app/static/swagger-ui && \
    curl -fsSL "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js" \
      -o /app/static/swagger-ui/swagger-ui-bundle.js && \
    curl -fsSL "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css" \
      -o /app/static/swagger-ui/swagger-ui.css && \
    curl -fsSL "https://cdn.jsdelivr.net/npm/redoc@2.1.5/bundles/redoc.standalone.js" \
      -o /app/static/swagger-ui/redoc.standalone.js

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
