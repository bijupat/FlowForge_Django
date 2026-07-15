# Production Dockerfile for FlowForge
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=flowforge.settings

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy project code
COPY . .

# Create directories if they don't exist
RUN mkdir -p /app/logs /app/uploads /app/generated /app/staticfiles

# Collect static files
RUN python manage.py collectstatic --noinput

# Expose port
EXPOSE 8000

# Run Gunicorn
CMD ["gunicorn", "flowforge.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4", "--access-logfile", "-", "--error-logfile", "-"]
