#!/bin/sh
# Kör i projektmappen:  sh diagnos.sh
# Klistra in hela utskriften. Inga lösenord skrivs ut.

COMPOSE="docker compose"
[ -f docker-compose.sqlite.yml ] && [ ! -f docker-compose.yml ] && COMPOSE="docker compose -f docker-compose.sqlite.yml"

line() { printf '\n=== %s ===\n' "$1"; }

line "1. Filer på plats"
for f in docker-compose.yml .env backend/Dockerfile frontend/index.html; do
  [ -e "$f" ] && echo "  OK      $f" || echo "  SAKNAS  $f"
done

line "2. .env (värden dolda, bara vilka nycklar som är satta)"
if [ -f .env ]; then
  while IFS= read -r rad; do
    case "$rad" in
      \#*|"") continue ;;
      *=) echo "  TOM     ${rad%=}" ;;
      *=*) nyckel="${rad%%=*}"; varde="${rad#*=}"
           echo "  satt    $nyckel (${#varde} tecken)" ;;
    esac
  done < .env
else
  echo "  .env saknas helt"
fi

line "3. Containrar"
$COMPOSE ps 2>&1

line "4. Publicerade portar"
$COMPOSE port app 8000 2>&1 || echo "  ingen portmappning hittad"
docker ps --filter name=borrjournal --format '  {{.Names}}  {{.Status}}  {{.Ports}}' 2>&1

line "5. Loggar från appen (40 sista raderna)"
$COMPOSE logs app --tail 40 2>&1

line "6. Loggar från databasen (15 sista raderna)"
$COMPOSE logs db --tail 15 2>&1 || echo "  ingen db-tjänst (SQLite-varianten?)"

line "7. Svarar appen inifrån containern?"
$COMPOSE exec -T app curl -s -m 5 -o /dev/null -w '  inifrån: HTTP %{http_code}\n' \
  http://localhost:8000/api/health 2>&1 || echo "  kunde inte köra curl i containern"

line "8. Svarar appen från värden?"
PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2)
PORT=${PORT:-8000}
for adress in 127.0.0.1 "$(hostname -I 2>/dev/null | awk '{print $1}')"; do
  [ -z "$adress" ] && continue
  svar=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://$adress:$PORT/api/health" 2>&1)
  echo "  http://$adress:$PORT -> ${svar:-ingen kontakt}"
done

line "9. Lyssnar något på porten?"
if command -v ss >/dev/null 2>&1; then
  ss -lntp | grep ":$PORT" || echo "  ingenting lyssnar på port $PORT"
elif command -v netstat >/dev/null 2>&1; then
  netstat -lntp | grep ":$PORT" || echo "  ingenting lyssnar på port $PORT"
else
  echo "  varken ss eller netstat finns, kan inte kontrollera (säger inget om felet)"
fi

line "10. Brandvägg"
ufw status 2>/dev/null | head -8 || echo "  ufw inte installerat"
iptables -L INPUT -n 2>/dev/null | head -6 || echo "  kan inte läsa iptables (kör med sudo för att se)"

line "11. Versioner"
docker --version 2>&1
docker compose version 2>&1

printf '\nKlart. Klistra in allt ovanstående.\n'
