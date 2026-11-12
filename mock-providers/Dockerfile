FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY mock-providers/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt
COPY mock-providers/app.py ./app.py

USER nobody
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8101"]
