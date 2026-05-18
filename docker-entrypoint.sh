#!/bin/bash
set -e
echo "Waiting for postgres..."
until pg_isready -h postgres -U radar; do
  sleep 1
done
echo "Running migration"
python scripts/migrate.py
echo "starting radar in mode: ${RADAR_MODE:-all}"
python src/main.py ${RADAR_MODE:-all}