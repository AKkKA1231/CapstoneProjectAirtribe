#!/bin/sh
set -eu

alembic upgrade head
prism-seed
exec uvicorn prism.main:app --host 0.0.0.0 --port 8000

