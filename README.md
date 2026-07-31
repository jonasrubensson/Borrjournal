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

## Filer och bilder

Både dokument- och bildfliken visar korten i rutnät med förhandsvisning, så det går att se vad som
är vad utan att öppna varje fil. Bilder skalas till 640 px vid uppladdning. För PDF renderas första
sidan som tumnagel, vilket gör att ett borrprotokoll går att känna igen på håll. DOCX och XLSX kan
inte förhandsvisas och får en tydlig typmarkering i stället.

Bilder tagna med telefonen får kameran direkt via bildfliken.

## Ändra och ta bort

| Vad | Var | Vem |
|---|---|---|
| Redigera kund | Knappen **Redigera kund** i kundhuvudet | tekniker, admin |
| Redigera anläggning, inklusive pumpuppgifter | Fliken **Anläggning** → **Redigera** | tekniker, admin |
| Byta ut en pump | Fliken **Anläggning** → **Byt pump** | tekniker, admin |
| Lägga till anläggning på befintlig kund | Fliken **Anläggning** → **Lägg till anläggning** | tekniker, admin |
| Ta bort anläggning | Fliken **Anläggning** → **Ta bort** | tekniker, admin |
| Stryka en journalanteckning | **Stryk** under anteckningen | tekniker, admin |
| Radera journalanteckning helt | **Radera** under anteckningen | admin |
| Radera kund med allt innehåll | **Redigera kund** → **Ta bort kunden helt** | admin |

### Pumpbyte i stället för överskrivning

**Byt pump** skriver först en journalrad med den gamla pumpen och dess serienummer, sedan sätts
den nya. Skriver du bara över modellen i redigeringsformuläret försvinner historiken, och då kan
du inte längre svara på frågan vilka kunder som haft en viss serie. Det är just den frågan
pumpflottan finns för.

### Journalen stryks, den redigeras inte

En anteckning som visar sig vara fel stryks med en angiven anledning. Texten står kvar, överstruken,
tillsammans med vem som strök den och när. Det går att ångra. Poängen med en journal är att det ska
gå att se vad som stod och vem som ändrade sig, annars är den inget värd som underlag i efterhand.
Rena felinmatningar kan en administratör radera på riktigt.

Att ta bort en anläggning raderar inte journalen. Anteckningarna ligger kvar på kunden, men lossas
från anläggningen. Påminnelser knutna till den försvinner.

### Uppgraderingar behåller data

Vid start läggs kolumner som tillkommit sedan databasen skapades till automatiskt. Bara tillägg,
aldrig ändringar eller borttag. Du kan alltså byta ut koden mot en nyare version utan att tömma
databasen. Byggs schemat om på riktigt behövs Alembic.

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

### Koordinater från adressen

I onboardingformuläret och i anläggningens redigeringsvy finns **Hämta från adressen**, som slår
upp koordinaten från adress, fastighetsbeteckning och kommun.

Det är det enda i appen som kräver internet. Det finns ingen rimlig väg att slå upp svenska
adresser offline utan att packa in ett adressregister. Standard är OpenStreetMaps Nominatim, vars
villkor kräver identifierbar User-Agent och högst en förfrågan per sekund, vilket respekteras.
Kör du eget Nominatim, peka om `GEOCODER_URL` i `.env`. Töm variabeln för att stänga av
funktionen helt.

Uppslaget hittar adressen, inte borrhålet. Det som visas är en startpunkt att justera, och
gränssnittet säger det rakt ut. För exakt läge på hålet: stå vid det och tryck **Hämta min
position**, eller skriv in koordinaten från borrprotokollet.

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

### Platstjänster kräver HTTPS

`navigator.geolocation` fungerar bara i säker kontext, alltså HTTPS eller `localhost`. Öppnar du
appen på `http://192.168.x.x` blockerar webbläsaren positionen utan att fråga. Detsamma gäller
notiser och installation på hemskärmen. Appen säger till om detta i klartext i stället för att
bara misslyckas, och koordinater går alltid att skriva eller klistra in för hand.

Fixa certifikatet i din reverse proxy, så fungerar både position och notiser.

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

### Textstorlek

Under **Inställningar → Notiser** finns fyra textstorlekar. Valet sparas per enhet, så en montör
kan köra stor text i mobilen medan kontorsdatorn står kvar på normal. Allt skalar med: rubriker,
tabeller, etiketter och knappar, så linjeringen håller även i största läget.

Inmatningsfält är aldrig mindre än 16 px, eftersom iOS annars zoomar in automatiskt när man
klickar i ett fält och lämnar sidan sned.

### Lägg till på hemskärmen

Samma ställe. Vad knappen gör beror på webbläsaren, och det är inget jag kan påverka:

* **Android, Chrome och Edge:** en riktig knapp som öppnar systemets installationsdialog.
* **iPhone och iPad:** Safari tillåter inte att en webbplats installerar sig själv, så här visas i
  stället tre steg: Dela → Lägg till på hemskärmen → öppna därifrån. Det måste göras från Safari,
  ingen annan webbläsare på iOS kan installera.
* **Utan HTTPS:** varken Android eller iOS erbjuder installation alls. Kortet säger det rakt ut.

### Tvåfaktor

Under **Inställningar → Notiser** kan varje användare slå på tvåfaktor för sitt eget konto.
Servern visar en QR-kod att skanna med Google Authenticator, Aegis, 1Password eller liknande, samt
nyckeln i klartext om kameran krånglar. Inget slås på förrän du bekräftat med en giltig kod, så du
kan inte låsa ute dig själv genom att avbryta.

Att stänga av kräver lösenordet, annars räcker en obevakad skärm för att ta bort skyddet. Har någon
tappat sin telefon kan en administratör nollställa tvåfaktor på kontot via
`PATCH /api/users/{id}` med `reset_totp`.

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
| DELETE | `/api/facilities/{id}` | Ta bort anläggning, journalen lossas men behålls |
| POST | `/api/facilities/{id}/pump-change` | Byt pump och journalför bytet |
| DELETE | `/api/customers/{id}` | Radera kund med allt, endast admin |
| PATCH | `/api/journal/{id}` | Stryk eller ångra strykning |
| DELETE | `/api/journal/{id}` | Radera anteckning, endast admin |
| GET | `/api/nearby` | Jobb nära en position eller en tolkad koordinat |
| GET | `/api/facilities/{id}/nearby` | Vad som kan slås ihop med resan dit |
| GET | `/api/coordinates/parse` | Tolkar inklistrad koordinat, för direktrespons i formuläret |
| GET | `/api/version` | Serverns version, gränssnittet varnar om de går isär |
| GET | `/api/geocode` | Koordinat från adress |
| GET | `/api/me/totp/qr` | QR-kod för att slå på tvåfaktor |
| POST | `/api/me/totp/disable` | Stäng av tvåfaktor, kräver lösenord |
| GET | `/api/audit` | Händelselogg, endast admin |

Interaktiv dokumentation finns på `/docs` när appen kör.

## Journalen

Varje anteckning hör till en anläggning. Har kunden flera brunnar går det annars inte att följa
vad som gjorts var, och då tappar journalen sitt värde som underlag. Har kunden ingen anläggning
ännu säger journalfliken till och länkar dit man lägger till en.

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

## När är Postgres värt besväret?

SQLite räcker längre än de flesta tror. Byt när något av det här stämmer, inte innan:

| Byt när | Varför |
|---|---|
| Fler än ungefär tio personer skriver samtidigt | SQLite serialiserar skrivningar. En skrivning tar millisekunder, så det är först vid många samtidiga som kön märks. |
| Du vill koppla Power BI, Excel eller ett bokföringssystem mot databasen | SQLite är en fil i en Docker-volym. Postgres har en port att koppla mot, med egna läsbehörigheter. |
| Registret passerar grovt räknat 100 000 anläggningar | Under det spelar det ingen roll. Ditt register lär hamna i tusental. |
| Du vill ha replikering eller återställning till en viss tidpunkt | SQLite har ingen motsvarighet till WAL-arkivering och standby. |
| Flera appinstanser ska dela databas | En SQLite-fil vill helst ha en skrivande process. |

Och lika viktigt: **byt inte** för att det känns proffsigare, eller för att komma runt ett problem
som inte är databasens fel. Två containrar är dubbelt så mycket att hålla vid liv, och ett
databaslösenord är en sak till som kan vara fel. Det kostade oss ett par kvällar redan.

För en borrfirma med några montörer och några tusen anläggningar är SQLite rätt val, med WAL
påslaget som nu. Blir det aktuellt att byta: sätt `POSTGRES_HOST`, `POSTGRES_USER` och
`POSTGRES_PASSWORD`, lägg till en `db`-tjänst i compose, ta en backup, packa upp den och kör
`python -m app.restore db.json` mot den nya databasen. Appen bygger anslutningssträngen själv och
kodar lösenordet korrekt, så tecken som `/` och `+` ställer inte till det.

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
