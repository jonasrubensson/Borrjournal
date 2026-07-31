# Borrjournal

Kund- och anläggningsregister för vattenborrning. Kunduppgifter, brunnsdata, pumpar, journal med
automatisk tidsstämpel, samt bilder och dokument. Körs som Docker-container och fungerar lika bra
på telefon som på dator.

## Komma igång

```bash
cd borrjournal
cp .env.example .env
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|;\
s|^BOOTSTRAP_PASSWORD=.*|BOOTSTRAP_PASSWORD=$(openssl rand -hex 12)|" .env
grep BOOTSTRAP_PASSWORD .env      # ditt första inloggningslösenord

docker compose up -d --build
./kontrollera.sh
```

`kontrollera.sh` säger rakt ut om appen fungerar, och visar loggen om något är fel.
Vill du ha demodata att klicka runt i: sätt `SEED_DEMO=true` i `.env` **innan** första starten.

En container, SQLite, ingen databas att lösenordsskydda. Allt ligger i volymen `data`:
databasfilen, uppladdade filer, tumnaglar och backuper.

### Reverse proxy framför

Appen lyssnar på porten i `APP_PORT` och terminerar ingen HTTPS. Sätt vilken proxy du vill framför
och peka den på värdens IP, den porten.

| Variabel | Standard | Betydelse |
|---|---|---|
| `APP_PORT` | `8000` | Porten på värden |
| `APP_BIND` | `0.0.0.0` | `127.0.0.1` om proxyn kör direkt på värden |

I Nginx Proxy Manager, Advanced-fliken, den viktiga raden först:

```nginx
client_max_body_size 30M;
proxy_read_timeout 300s;

add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "same-origin" always;
```

Utan `client_max_body_size` stryper nginx uppladdningar långt under 25 MB, och det syns bara som
att uppladdningen dör mitt i. Stäng porten utifrån i brandväggen, annars går den att nå förbi
HTTPS.

**HTTPS krävs för notiser.** Service workers och webbpush fungerar bara över HTTPS, med undantag
för `localhost`. Utan certifikat fungerar allt annat som vanligt, bara notiserna uteblir.

### Inga externa beroenden

Gränssnittet hämtar ingenting från internet: inga typsnitt från CDN, inga bibliotek. Appen
fungerar lika bra på ett internt nät utan internetuppkoppling. Går något ändå fel vid start visas
felet i en röd list längst ner i stället för att sidan blir tom.

### Postgres senare

Byt till Postgres genom att sätta `POSTGRES_HOST`, `POSTGRES_USER` och `POSTGRES_PASSWORD` i
miljön och lägga till en `db`-tjänst i compose. Appen bygger anslutningssträngen själv och kodar
lösenordet korrekt. Flytta data genom att ta en backup, packa upp den och köra
`python -m app.restore db.json` mot den nya databasen.

## Jobb i närheten

Två situationer, samma underlag:

* **Du står någonstans.** Tryck på plats-ikonen i toppfältet, sedan *Använd min position*.
  Anläggningar inom vald radie listas med avstånd, riktning och varför de dyker upp.
* **Du planerar en resa.** Öppna kunden du ska till. Längst ned visas *Slå ihop med resan* med
  det som ligger inom tre mil från den anläggningen.

Sorteringen sätter angelägenhet före avstånd. En försenad service två mil bort hamnar före en
fungerande brunn på samma gata, eftersom det är den avstickaren som faktiskt är värd något.
Bocka i stoppen och tryck *Öppna rundan i kartan*, så byggs en Google Maps-rutt med din position
som start och stoppen som delmål (högst tio, det är kartans gräns).

### Koordinater

Anläggningar lagrar WGS84 som decimaltal, men inmatningsfältet tolkar det du klistrar in:

| Du skriver | Tolkas som |
|---|---|
| `59.7231, 18.9412` | decimalgrader |
| `59,7231 18,9412` | decimalgrader med svenskt decimaltecken |
| `N 6620123 E 674321` | SWEREF 99 TM, räknas om |
| `E 674321 N 6620123` | samma, oavsett ordning |
| `59°43.386'N 18°56.472'E` | grader och minuter |

Tolkningen visas direkt under fältet medan du skriver, så du ser att den blev rätt innan du
sparar. Ute vid borrhålet finns knappen *Hämta min position*, som tar koordinaten från telefonens
GPS.

Omvandlingen SWEREF 99 TM ↔ WGS84 är Gauss-Krügers formler för GRS 80. Den är verifierad på två
sätt: rundgång fram och tillbaka avviker mindre än en hundradels millimeter, och en punkt på
centralmeridianen (15°E) ger exakt E = 500000, vilket den ska per definition. Den är däremot inte
kontrollerad mot Lantmäteriets officiella testpunkter.

**Det här kan inte göras:** appen kan inte meddela dig av sig själv när du råkar köra förbi ett
jobb. Webbläsare tillåter inte att en webbapp läser positionen i bakgrunden, av goda skäl. Det
kräver en riktig app på telefonen. Det appen kan är att svara direkt när du frågar, och det gör
den på ett par sekunder.

## Arkitektur

```
Nginx Proxy Manager (443) → app (FastAPI + uvicorn) → Postgres
                                  ↓
                     /data/files, /data/thumbs, /data/backups   (volym "files")
```

| Del | Val | Varför |
|---|---|---|
| Backend | FastAPI, SQLAlchemy async | Liten kodbas, typade scheman, snabb |
| Databas | Postgres 16 | Riktig relationsdatabas, `pg_dump` för backup |
| Filer | Volym på disk, metadata i databasen | Filer i databasen gör dumpar tunga och långsamma |
| Frontend | Vanilla JS, tre filer | Ingen build-kedja att underhålla om två år |
| HTTPS | Extern reverse proxy | Du har redan Nginx Proxy Manager, appen ska inte duplicera det |
| Autentisering | JWT + bcrypt, valfri TOTP | Samma mönster som dokumentationsplattformen |
| Notiser | Webbpush (VAPID) + SMTP | Inga tredjepartstjänster, inget konto att skapa |
| Schemaläggning | asyncio-loop i appen | Inget Celery, ingen extra container att hålla vid liv |

### Roller

| Roll | Får |
|---|---|
| `admin` | Allt, inklusive konton och händelselogg |
| `tekniker` | Läsa och skriva kunder, journal, filer |
| `lasare` | Bara läsa |

### Sökning över hela registret

Sökfältet i toppen träffar kundnamn, kundnummer, fastighet, kommun, brunns-ID, pumptillverkare,
pumpmodell, serienummer, journaltext och filnamn i samma anrop (`GET /api/search?q=`).

För fabriksfel-fallet finns dessutom två egna vyer:

* **Pumpar** – en rad per tillverkare och modell med antal och installationsspann. Klick på en rad
  filtrerar anläggningslistan på just den modellen.
* **Anläggningar** – filter på tillverkare, modell, typ, status och installationsdatum, med
  CSV-export av träfflistan (`GET /api/facilities.csv`). Det är underlaget du skickar till
  leverantören eller använder för utskick till berörda kunder.

Tillverkare, modell och serienummer ligger i egna kolumner med index, inte i ett fritextfält, just
för att den sortens fråga ska vara exakt och snabb.

## API i korthet

| Metod | Väg | Gör |
|---|---|---|
| POST | `/api/login` | Loggar in. Svarar `428` om kontot kräver engångskod |
| GET | `/api/dashboard` | Räknare, servicebehov, senaste journalrader |
| GET/POST | `/api/customers` | Lista och skapa kunder |
| GET/PATCH | `/api/customers/{id}` | Läsa och ändra kund |
| POST | `/api/customers/{id}/facilities` | Ny anläggning på befintlig kund |
| GET/PATCH | `/api/facilities` `/api/facilities/{id}` | Flottlista med filter, uppdatera anläggning |
| GET | `/api/pumps` | Aggregerad pumpflotta per modell |
| GET | `/api/facilities.csv` | CSV-export av filtrerad flotta |
| GET/POST | `/api/customers/{id}/journal` | Journal. Tidsstämpel och signatur sätts av servern |
| POST | `/api/customers/{id}/files` | Uppladdning av PDF, DOCX, XLSX, bild |
| GET | `/api/files/{id}` `/api/files/{id}/thumb` | Hämta fil respektive tumnagel |
| POST | `/api/onboarding` | Skapar kund + anläggning + första journalraden i ett anrop |
| GET/POST | `/api/reminders` | Lista och skapa påminnelser |
| PATCH | `/api/reminders/{id}` | Kvittera, skjut fram, ändra |
| POST | `/api/reminders/scan` | Kör automatgenereringen direkt |
| GET/POST | `/api/backups` | Lista och skapa backup, endast admin |
| GET | `/api/backups/{id}/download` | Ladda ner en backup |
| GET | `/api/backups/{id}/restore-guide` | Exakta återställningskommandon för filen |
| GET/PUT | `/api/backups/schedule` | Nattlig backup: tid och gallring |
| GET/PUT | `/api/notifications/email` | SMTP-inställningar, lösenordet returneras aldrig |
| POST | `/api/notifications/push/subscribe` | Registrera enhet för notiser |
| GET | `/api/nearby` | Jobb nära en position eller en tolkad koordinat |
| GET | `/api/facilities/{id}/nearby` | Vad som kan slås ihop med resan dit |
| GET | `/api/coordinates/parse` | Tolkar inklistrad koordinat, för direktrespons i formuläret |
| GET | `/api/audit` | Händelselogg, endast admin |

Interaktiv dokumentation finns på `/docs` när appen kör.

## Journalen

Journalrader tidsstämplas av servern, aldrig av klienten, och signeras med den inloggade
användaren. Rader ändras inte i efterhand: en rättelse skapas som en ny rad med `corrects_id`
satt till originalet. Det gör att en journal går att visa upp i efterhand utan att någon behöver
lita på att ingen redigerat historiken.

## Telefon

* Navigationen blir en flikrad längst ner, tabeller blir kort.
* Bildfliken använder `capture="environment"`, så kameran öppnas direkt.
* Bilder skalas till 640 px tumnaglar vid uppladdning, så listor går snabbt på mobildata.
* Telefonnummer och e-post är klickbara för att ringa och mejla direkt från kundkortet.
* Positionsknappen använder telefonens GPS, både för att hitta jobb i närheten och för att sätta
  koordinaten på en ny anläggning ute i fält.

## Utveckling utan Docker

```bash
cd backend
pip install -r requirements.txt
export SECRET_KEY=dev SEED_DEMO=true BOOTSTRAP_PASSWORD=devlosenord123 FRONTEND_DIR=../frontend
uvicorn app.main:app --reload
```

Standarddatabasen är då SQLite i `backend/borrjournal.db`, ingen Postgres behövs.

## Att veta om driften

**Kör appen med en (1) worker.** Schemaläggaren ligger i processen, så två workers gör samma
backup två gånger. Behöver du fler workers senare får schemaläggaren flyttas till en egen
container med ett lås.

**Backuperna ligger i samma volym som filerna** (`/data/backups`). Det skyddar mot misstag i
databasen, inte mot att disken dör. Montera en extern sökväg eller kopiera bort dumparna
regelbundet.

## Nästa steg som inte är gjorda

* **Databasmigreringar.** Tabeller skapas med `create_all` vid start. Innan första
  produktionsändringen av schemat, lägg in Alembic.
* **Fritextsök i filinnehåll.** Idag söks filnamn och beskrivning, inte texten inne i PDF:er.
* **HEIC.** iPhone-bilder tas emot, men tumnagel skapas inte utan `pillow-heif`.
* **Token i localStorage.** Fungerar bra för ett internt verktyg. Vill du hårdare skydd mot XSS,
  byt till httpOnly-cookie med CSRF-token.
* **Serviceintervall** räknas kalendermässigt för påminnelser, men statusfärgen på Översikt
  använder fortfarande 30 dagar per månad. Skillnaden är några dagar, men de två bör slås samman.
* **SMS** finns inte. Push och e-post täcker det mesta, men en montör utan appen installerad nås
  bara av mejl.
