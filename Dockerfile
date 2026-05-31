FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Expose port (gunicorn)
EXPOSE 7890

# Start with gunicorn
CMD ["gunicorn", "wsgi:app", "-b", "0.0.0.0:7890", "-w", "4", "--access-logfile", "-", "--error-logfile", "-"]
