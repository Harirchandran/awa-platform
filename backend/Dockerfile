# AWA Backend Dockerfile
# Multi-stage build for optimized production image
# ===============================
# Stage 1: Builder
# ===============================
FROM python:3.12-slim AS builder
WORKDIR /app
# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*
# Copy requirements first for layer caching
COPY requirements.txt .
# Install Python dependencies
RUN pip install --no-cache-dir --user -r requirements.txt
# ===============================
# Stage 2: Production
# ===============================
FROM python:3.12-slim AS production
# Create app directory structure matching import paths
WORKDIR /app
# Create non-root user for security
RUN groupadd -r awa && useradd -r -g awa awa
# Copy installed packages from builder
COPY --from=builder /root/.local /home/awa/.local
# Make sure scripts in .local are usable
ENV PATH=/home/awa/.local/bin:$PATH
# Copy application code into /app/backend
COPY . /app/backend/
# Set Python path to find 'backend' module
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
# Change ownership
RUN chown -R awa:awa /app
# Switch to non-root user
USER awa
# Expose port
EXPOSE 8000
# Default command: Run FastAPI with uvicorn
CMD ["uvicorn", "backend.api.app:app", "--host", "0.0.0.0", "--port", "8000"]