#!/usr/bin/env bash
# Uppdaterar Borrjournal på ett sätt som inte tappar data eller hemligheter.
#
#   ./uppdatera.sh
#
# Körs från projektkatalogen, efter att du hämtat den nya koden dit.
# Skriptet gör i tur och ordning:
#
#   1. kontrollerar att .env finns och skapar en vid behov
#   2. tar en backup av datavolymen innan något rörs
#   3. bygger om och startar
#   4. väntar tills appen svarar
#   5. jämför backend och gränssnitt och säger till om de inte är i takt
#
# Radera aldrig projektkatalogen med rm -rf. Där ligger .env, och utan den går
# alla inloggningar sönder eftersom SECRET_KEY byts ut.

set -uo pipefail

GRON=$'\033[0;32m'; GUL=$'\033[0;33m'; ROD=$'\033[0;31m'; TYST=$'\033[0;90m'; NOLL=$'\033[0m'
ok()   { echo "  ${GRON}✓${NOLL} $1"; }
varn() { echo "  ${GUL}!${NOLL} $1"; }
fel()  { echo "  ${ROD}✗${NOLL} $1"; }

if [ ! -f docker-compose.yml ]; then
  fel "Ingen docker-compose.yml här. Kör skriptet från projektkatalogen."
  exit 1
fi

PROJEKT=$(basename "$PWD")
echo
echo "Uppdaterar Borrjournal i $PWD"
echo "${TYST}Projektnamnet styr vilken datavolym som används: ${PROJEKT}_data${NOLL}"
echo

# ---- 1. hemligheter ----
echo "1. Konfiguration"
if [ -f .env ]; then
  ok ".env finns, hemligheterna behålls"
  if ! grep -q "^SECRET_KEY=..*" .env; then
    fel "SECRET_KEY saknar värde i .env"
    exit 1
  fi
else
  varn ".env saknas, skapar en ny"
  if [ ! -f .env.example ] && [ ! -f env.example ]; then
    fel "Varken .env.example eller env.example finns. Kan inte skapa .env."
    exit 1
  fi
  cp .env.example .env 2>/dev/null || cp env.example .env
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" .env
  sed -i "s|^BOOTSTRAP_PASSWORD=.*|BOOTSTRAP_PASSWORD=$(openssl rand -hex 12)|" .env
  varn "Nya nycklar skapade. Alla blir utloggade och behöver logga in på nytt."
  echo "     Startlösenord för admin (bara om kontot saknas):"
  echo "     $(grep '^BOOTSTRAP_PASSWORD=' .env | cut -d= -f2)"
fi

# ---- 2. backup före ----
echo
echo "2. Säkerhetskopia före uppdateringen"
if docker volume inspect "${PROJEKT}_data" >/dev/null 2>&1; then
  mkdir -p ./backuper-innan-uppdatering
  STAMPEL=$(date +%Y-%m-%d_%H%M%S)
  if docker run --rm \
      -v "${PROJEKT}_data":/data:ro \
      -v "$PWD/backuper-innan-uppdatering":/ut \
      alpine tar czf "/ut/data-${STAMPEL}.tar.gz" -C /data . 2>/dev/null; then
    STORLEK=$(du -h "./backuper-innan-uppdatering/data-${STAMPEL}.tar.gz" | cut -f1)
    ok "data-${STAMPEL}.tar.gz sparad ($STORLEK)"
  else
    varn "Kunde inte ta kopian. Avbryter hellre än att riskera datan."
    exit 1
  fi
else
  varn "Ingen datavolym ${PROJEKT}_data hittad. Är det här en förstagångsinstallation?"
  varn "Om du har data sedan tidigare: kontrollera att katalogen heter samma som förut."
  read -r -p "  Fortsätt ändå? (j/N) " svar
  [ "$svar" = "j" ] || exit 1
fi

# ---- 3. bygg och starta ----
echo
echo "3. Bygger och startar"
docker compose down --remove-orphans >/dev/null 2>&1
if ! docker compose build --no-cache 2>&1 | tail -3; then
  fel "Bygget misslyckades"
  exit 1
fi
docker compose up -d >/dev/null 2>&1 || { fel "Kunde inte starta"; exit 1; }
ok "Containern startad"

# ---- 4. vänta in appen ----
echo
echo "4. Väntar på att appen svarar"
PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | cut -d= -f2)
PORT=${PORT:-8000}
for i in $(seq 1 45); do
  if curl -sf -m 2 "http://localhost:${PORT}/api/health" >/dev/null 2>&1; then
    ok "Appen svarar på port ${PORT}"
    KLAR=1
    break
  fi
  sleep 2
done
if [ -z "${KLAR:-}" ]; then
  fel "Appen svarar inte efter 90 sekunder"
  echo
  docker compose logs app --tail 30
  exit 1
fi

# ---- 5. samma version i båda delarna ----
echo
echo "5. Kontrollerar versionerna"
SVAR=$(curl -s "http://localhost:${PORT}/api/version")
BACK=$(echo "$SVAR" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')
FRAM=$(echo "$SVAR" | sed -n 's/.*"ui_version":"\([^"]*\)".*/\1/p')

echo "     backend:     ${BACK:-okänd}"
echo "     gränssnitt:  ${FRAM:-okänd}"

if [ -n "$BACK" ] && [ "$BACK" = "$FRAM" ]; then
  ok "Delarna är i takt"
else
  fel "Delarna är INTE i takt"
  echo
  echo "     Vanligaste orsaken: bara vissa filer byttes ut. Gränssnittet läses"
  echo "     från ./frontend på disken, backend byggs in i imagen. Byt ut båda."
  echo
  echo "     Kontrollera vad som ligger här:"
  echo "       grep UI_VERSION frontend/app.js"
  echo "       grep APP_VERSION backend/app/version.py"
  exit 1
fi

echo
echo "  Klart. Öppna appen och tryck ${GRON}Hämta om nu${NOLL} om listen om version syns"
echo "  ${TYST}(webbläsaren kan ha kvar en gammal flik)${NOLL}"
echo
