# ERGAVON application image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Static files are baked into the image; WhiteNoise serves them
RUN SECRET_KEY=build-only DEBUG=False python manage.py collectstatic --noinput --verbosity 0

RUN useradd --create-home --uid 1000 ergavon \
    && mkdir -p /app/media /app/tmp \
    && chown -R ergavon:ergavon /app/media /app/tmp
USER ergavon

EXPOSE 8000
CMD ["python", "docker/start.py"]
