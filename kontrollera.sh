#!/bin/sh
# Kör efter "docker compose up -d". Säger rakt ut om appen fungerar eller inte.
set -e
PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2)
PORT=${PORT:-8000}
echo "Kontrollerar Borrjournal på port $PORT"

printf "  container igång ....... "
docker compose ps --status running 2>/dev/null | grep -q borrjournal-app && echo "ja" || { echo "NEJ"; echo; docker compose logs app --tail 30; exit 1; }

printf "  api svarar ............ "
sleep 2
if curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1; then echo "ja"; else
  echo "NEJ"; docker compose logs app --tail 30; exit 1; fi

printf "  inloggningssidan ...... "
curl -fsS "http://localhost:$PORT/" 2>/dev/null | grep -q "Borrjournal" && echo "ja" || { echo "NEJ"; exit 1; }

printf "  app.js och styles.css . "
curl -fsS "http://localhost:$PORT/static/app.js" >/dev/null 2>&1 && \
curl -fsS "http://localhost:$PORT/static/styles.css" >/dev/null 2>&1 && echo "ja" || { echo "NEJ"; exit 1; }

printf "  inloggning fungerar ... "
PW=$(grep -E '^BOOTSTRAP_PASSWORD=' .env | cut -d= -f2-)
USER=$(grep -E '^BOOTSTRAP_ADMIN=' .env | cut -d= -f2-)
USER=${USER:-admin}
if curl -fsS -X POST "http://localhost:$PORT/api/login" -H 'Content-Type: application/json' \
   -d "{\"username\":\"$USER\",\"password\":\"$PW\"}" 2>/dev/null | grep -q token; then
  echo "ja"
else
  echo "NEJ - kontrollera BOOTSTRAP_PASSWORD i .env"; exit 1
fi

echo
echo "Allt fungerar. Öppna http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT och logga in som $USER."
