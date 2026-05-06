FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt .
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/* && pip install -r requirements.txt 
COPY . .
RUN chmod +x docker-entrypoint.sh
CMD ["./docker-entrypoint.sh"]