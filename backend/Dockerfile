FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIR=/app/frontend

WORKDIR /app

# Medvetet inga extra apt-paket. Varje rad som hämtar från ett externt repo är en rad
# som kan fälla bygget en tisdagsmorgon. Backupen använder en logisk JSON-dump som
# innehåller allt och läses tillbaka med "python -m app.restore".
# Vill du ha en native pg_dump-fil vid sidan om, kör den från db-containern:
#   docker compose exec -T db pg_dump -U borrjournal -Fc borrjournal > db.dump
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Sökvägarna utgår från projektroten, eftersom byggkontexten är hela katalogen.
# Det behövs för att gränssnittet ska kunna bakas in i samma image.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app

# Gränssnittet bakas in i imagen. Monterades det från disken kunde backend och
# gränssnitt gå isär vid en uppdatering, vilket var svårt att upptäcka. Nu följs
# de alltid åt, och docker compose pull räcker för att uppdatera.
COPY frontend ./frontend

# Kör som vanlig användare
RUN useradd -m -u 10001 borr && mkdir -p /data && chown -R borr:borr /app /data
USER borr

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
