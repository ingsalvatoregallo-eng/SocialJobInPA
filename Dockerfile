# Immagine unica per app / worker / scheduler di SocialJobInPA (il comando
# cambia per servizio, vedi docker-compose.yml). Progetto separato da
# JobInPA (che gira sulla VM Aruba): si parla solo via API HTTP private,
# vedi src/social/jobinpa_client.py. Compatibile con Docker Desktop + WSL2.

FROM python:3.12-slim

# Font per il rendering deterministico dei template immagine (Pillow):
# senza, i testi cadrebbero sul font bitmap di fallback.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY tests/ ./tests/
COPY assets/ ./assets/

# data/ e assets/generated/ sono volumi (docker-compose.yml): il DB SQLite
# e gli asset generati sopravvivono ai rebuild.
RUN mkdir -p data assets/generated assets/brand

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]
