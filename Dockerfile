FROM python:3.11-slim

WORKDIR /app

# Install system deps for psycopg2 build
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8788

CMD ["python", "server.py", "--port", "8788", "--shared", "--no-open"]
