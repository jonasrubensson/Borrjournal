# Borrjournal

Kund- och anläggningsregister för vattenborrning. Kunduppgifter, brunnsdata, pumpar, journal med
automatisk tidsstämpel, samt bilder och dokument. Körs som Docker-container och fungerar lika bra
på telefon som på dator.

## Komma igång

Det finns ingen `.env` i arkivet, bara `.env.example`. Det är med avsikt: den innehåller
lösenord och ska aldrig följa med en fil du skickar vidare. Skapa den så här:

```bash
cd borrjournal
cp .env.example .env

# generera hemligheter och skriv in dem i .env
echo "SECRET_KEY=$(openssl rand -base64 36)"
echo "POSTGRES_PASSWORD=$(openssl rand -base64 24)"
echo "BOOTSTRAP_PASSWORD=$(openssl rand -base64 18)"

docker compose up -d --build
```

Eller i ett svep, om du vill slippa klistra:

```bash
cp .env.example .env
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -base64 36)|;\
s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -base64 24)|;\
s|^BOOTSTRAP_PASSWORD=.*|BOOTSTRAP_PASSWORD=$(openssl rand -base64 18)|" .env
grep BOOTSTRAP_PASSWORD .env    # detta är ditt första inloggningslösenord
docker compose up -d --build
```

Compose vägrar starta om `POSTGRES_PASSWORD`, `SECRET_KEY` eller `BOOTSTRAP_PASSWORD` är tomma,
så du får ett tydligt fel i stället för en osäker installation.

Öppna `http://localhost:8000` och logga in med `admin` och det lösenord du satte i
`BOOTSTRAP_PASSWORD`. Byta lösenord gör du via `POST /api/me/password` eller genom att skapa ett
eget konto under **Konton** och stänga av admin-kontot.

Vill du testa med demodata i en tom databas: sätt `SEED_DEMO=true` innan första starten.
Sju kunder läggs in, varav tre delar pumpmodell så att flottfiltret blir meningsfullt att prova.

### Bakom Nginx Proxy Manager

Appen terminerar ingen HTTPS själv, det gör NPM. Två sätt att koppla ihop dem:

**Delat Docker-nätverk (rekommenderas).** Skapa nätverket en gång, lägg NPM på det, och kommentera
bort `ports`-blocket i `docker-compose.yml`. Då är appen inte nåbar från värden alls.

```bash
docker network create proxy
```

I NPM: **Proxy Hosts → Add Proxy Host**

| Fält | Värde |
|---|---|
| Domain Names | din domän |
| Scheme | `http` |
| Forward Hostname | `borrjournal-app` |
| Forward Port | `8000` |
| Block Common Exploits | på |
| Websockets Support | behövs inte, appen använder inga |
| SSL | begär Let's Encrypt-certifikat, slå på **Force SSL** och **HTTP/2** |

**Utan delat nätverk.** Låt `APP_BIND` stå kvar och peka NPM på värdens Docker-brygga
(`172.17.0.1`) eller LAN-adress, port 8000.

#### Advanced-fliken i NPM

Klistra in det här. Den första raden är den viktiga: nginx stryper annars uppladdningar långt
under 25 MB, och felet syns bara som en avbruten uppladdning.

```nginx
client_max_body_size 30M;
proxy_read_timeout 300s;

add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "same-origin" always;
```

Håll `client_max_body_size` något högre än `MAX_UPLOAD_MB` i `.env`, så att det är appen som
avvisar för stora filer med ett begripligt meddelande i stället för nginx med ett 413.

**HTTPS är inte valfritt om du vill ha notiser.** Service workers och webbpush fungerar bara över
HTTPS (undantag: `localhost` vid utveckling). Ordna certifikatet i NPM innan du testar notiser på
telefonen.

### Backup och återställning

Backup styrs från **Inställningar → Backup** i gränssnittet, som admin. Där kan du:

* skapa en backup direkt och ladda ner den till din dator,
* slå på nattlig automatisk backup med valfri tid och hur många dagar som ska sparas,
* se storlek, status och vilken motor som användes.

Varje backup är en `tar.gz` med databasdump, alla uppladdade filer, tumnaglar och ett
`manifest.json`. Motorn är `pg_dump` när appen kör mot Postgres, annars en logisk JSON-dump.
De tre senaste behålls alltid, även om de är äldre än gränsen.

**Återställning görs från terminalen, inte i webben.** Det är ett medvetet val: en knapp som skriver
över hela databasen är för lätt att trycka på av misstag, och appen kan inte läsa in en dump i den
databas den själv har öppen. Klicka **Återställ** på en backup så visas de exakta kommandona för
just den filen, med en kopiera-knapp. I korthet:

```bash
docker compose stop app
docker compose cp app:/data/backups/borrjournal-....tar.gz ./
tar -xzf borrjournal-....tar.gz
cat db.dump | docker compose exec -T db pg_restore -U borrjournal -d borrjournal --clean --if-exists
docker compose cp files/. app:/data/files/
docker compose start app
```

Återläsning av båda formaten är testad mot en tom databas, inte bara dokumenterad.

## Påminnelser

Fyra typer skapas automatiskt från anläggningens datum, en gång per dygn och vid **Kör
genomsökning**:

| Typ | Beräknas från | Förvarning |
|---|---|---|
| Service | senaste service + serviceintervall | 30 dagar |
| Vattenprov | provdatum + giltighetstid (36 mån som standard) | 30 dagar |
| Intyg | utgångsdatum på anläggningen | 45 dagar |
| Uppföljning | datumfältet i journalrutan | på dagen |

Egna påminnelser lägger du till i påminnelsevyn, med eller utan koppling till en kund. Automatiska
påminnelser dubbleras inte: varje kombination av typ, anläggning och datum har en nyckel, så en ny
genomsökning skapar inget som redan finns. Kvittera med **Klar** eller skjut fram med **+7 d**.

Utskicket är ett samlat meddelande per körning, inte ett per rad. Både e-post och push går ut
samtidigt, och raden märks med vilka kanaler som användes.

## Notiser på telefonen

Appen är en PWA. Lägg till den på hemskärmen och notiser fungerar även när webbläsaren är stängd.

* **Android och Chrome/Edge på dator:** fungerar direkt, slå på notiser i påminnelsevyn.
* **iPhone och iPad:** kräver iOS 16.4 eller senare, och att appen läggs till på hemskärmen först
  (Dela → Lägg till på hemskärmen). Webbpush fungerar inte i Safari-fliken, bara i den installerade
  appen. Gränssnittet upptäcker detta och visar instruktionen i stället för en knapp som inte
  fungerar.
* **HTTPS krävs** för både service worker och push. Kör därför `--profile proxy` med Caddy, eller
  motsvarande, innan du testar notiser på telefonen. `localhost` fungerar för utveckling.

Nycklarna för push (VAPID) genereras av servern första gången någon efterfrågar dem. Den privata
nyckeln lämnar aldrig servern och returneras inte av API:et.

E-post konfigureras under **Inställningar → Notiser**: server, port, kryptering, inloggning,
avsändare och mottagare, med en knapp för testmejl. SMTP-lösenordet returneras aldrig av API:et,
men det lagras i databasen, så en databasdump innehåller det. Vill du undvika det, peka mot en
intern relay som inte kräver inloggning.

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
