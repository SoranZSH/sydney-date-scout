FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN [ -f config.yaml ] || cp config.example.yaml config.yaml

VOLUME ["/app/data"]

CMD ["python", "run.py", "serve"]
