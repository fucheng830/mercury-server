FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8788

CMD ["python", "server.py", "--port", "8788", "--shared", "--no-open"]
