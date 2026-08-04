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

Bilder och dokument hämtas med inloggningstoken och läggs in som blob-URL:er, eftersom en vanlig
`<img src>` inte kan skicka Authorization-headern och därför fick 401. Alternativet, att lägga
token i frågesträngen, hade hamnat i webbserverloggar och referrers. Bara det som syns på skärmen
hämtas, så en kund med femtio foton drar inte hem allt på en gång.


Både dokument- och bildfliken visar korten i rutnät med förhandsvisning, så det går att se vad som
är vad utan att öppna varje fil. Bilder skalas till 640 px vid uppladdning. För PDF renderas första
sidan som tumnagel, vilket gör att ett borrprotokoll går att känna igen på håll. DOCX och XLSX kan
inte förhandsvisas och får en tydlig typmarkering i stället.

Bilder tagna med telefonen får kameran direkt via bildfliken.

## Så är appen upplagd

Navigeringen följer dagen, inte databasen. Fem ställen räcker för det mesta:

| | Vad du gör där |
|---|---|
| **Idag** | Vad som behöver hanteras: förfallna serviceärenden, senaste händelser |
| **Kunder** | Sök upp en kund, se allt om den |
| **Besök** | Inbokade platsbesök, med underlag om grannbrunnar |
| **Fakturera** | Utfört men ofakturerat, fakturerat men obetalt |
| **Mer** | Artiklar, mallar, pumpflotta, påminnelser, inställningar |

**Kundkortet** har fem flikar, och öppnas på **Översikt** som svarar på vad som är läget:
vad som behöver göras, senaste journalanteckningarna och de senaste bilderna, med knappar för
det man oftast vill göra härnäst.

| Flik | Innehåll |
|---|---|
| Översikt | Att göra, senaste händelser, snabbknappar |
| Journal | Alla anteckningar |
| Ekonomi | Offerter och arbetsorder, samma flöde |
| Filer | Foton och dokument med filter |
| Anläggning | Brunnsdata, pump, service |

Dokument och bilder låg tidigare på var sin flik trots att de är samma sak, och offerter och order
på var sin trots att de är ett flöde. Det blev sju flikar som tvingade fram klickande för att få
ihop bilden. Nu är de fyra plus en översikt.

### I fält och på kontoret

Samma app, olika ingångar. Ute används **Besök** för att förbereda, kameraknappen på filfliken för
att dokumentera, och arbetsordern för att fylla i vad som gick åt medan man minns det. På kontoret
används **Fakturera** för att se vad som ska ut, och **Kunder** för att söka.

## Offert, arbetsorder och artiklar

### Varför inte bara journalen?

Journalen berättar vad som hände: berättande, oföränderlig, signerad. Den duger inte till att
summera vad som ska faktureras. Därför finns **arbetsordern**, med strukturerade rader som har
antal, pris och moms. De två hänger ihop: när en offert skickas eller en order sparas som PDF
skrivs en journalrad om det.

| | Journal | Arbetsorder |
|---|---|---|
| Svarar på | vad hände | vad kostade det |
| Format | fritext med tidsstämpel | rader med antal och pris |
| Ändras | nej, stryks | ja, tills den fakturerats |

### Artikelregister

**Artiklar** i menyn: det du har hemma i lager och det du brukar debitera. Varje artikel har
inköps- och försäljningspris, enhet, moms, lagersaldo och en min-nivå som ger varning. Marginalen
räknas ut, så en felprissatt artikel syns direkt.

När du skriver en fritextrad på en offert eller arbetsorder letar systemet efter liknande
artiklar och visar dem under fältet, så att samma sak inte hamnar i registret under fem olika
namn. Träffen går att använda direkt. Är benämningen ny får du frågan om att lägga upp den som
artikel när raden sparats, med pris och enhet ifyllt.

Saldot ändras aldrig direkt, bara genom lagerrörelser med anledning och anteckning. Varje saldo
går därför att förklara i efterhand. Artiklar som utgår avaktiveras i stället för att raderas, så
att gamla order står kvar oförändrade.

Tjänster som borrning och etablering läggs upp med **lagerförs ej**. De prissätts som allt annat
men rör inte lagret.

### Logotyp och företagsnamn

**Inställningar → Företag**: ladda upp en logotyp, så visas den i appen, på inloggningssidan och
överst på varje offert och arbetsorder. PNG med genomskinlig bakgrund blir bäst. Bilden skalas
automatiskt och genomskinligheten bevaras, så ingen vit ruta hamnar runt den i PDF:en.

Företagsnamnet ersätter "Borrjournal" i gränssnittet.

### Offertmallar

Tre mallar finns från start: bergborrad brunn med pump, energibrunn för bergvärme, och pumpbyte.
Varje mall har rubrik, inledande text, villkor och färdiga rader.

När du skapar en offert väljer du mall, och allt fylls i. **Raderna matchas mot artikelregistret
på namn och nummer**, så dagens pris används där artikeln finns. Rader som inte matchar behåller
mallens pris i stället för att tyst försvinna.

Allt går att ändra efteråt. Ändrar du en standardmall blir den er egen. Har du gjort en offert du
är nöjd med finns **Spara som mall** på offerten, vilket är det enklaste sättet att få egna mallar.
Mallarna hanteras under **Mer → Offertmallar**.

### Tre vägar till en offert

| Situation | Så gör du |
|---|---|
| Befintlig kund | Kundkortet, fliken Ekonomi, Ny offert |
| Efter platsbesök | Besöket, Skapa offert |
| Någon ringer och vill ha pris | Besök, **Offert på förfrågan** |

Den sista är för telefonsamtal. Ingen kund läggs upp, inget besök bokas, bara ett namn och
gärna en e-postadress. Blir det affär trycker du **Blev kund** på offerten, så skapas kund och
anläggning och en journalrad noterar vilken offert de kom ur. Blir det inget ligger offerten
kvar som avslagen utan att skräpa ner kundregistret.

### Förhandsgranskning medan du bygger

På offert och arbetsorder finns **Visa förhandsgranskning**, som lägger PDF:en längst ned på
sidan och uppdaterar den när du lägger till rader eller skriver texter. Valet kommer ihåg sig
till nästa gång.

Det är serverns PDF som visas, samma fil som kunden får. Alternativet hade varit att härma
layouten i HTML, men då visär förhandsgranskningen något annat än det som skickas, vilket är
värre än ingen förhandsgranskning alls.

### Offert

Skapas på en kund eller direkt på ett platsbesök, innan kunden finns. Rader hämtas ur
artikelregistret, som fyller i benämning, pris och enhet. Priset kopieras till raden, så att en
prisändring i registret inte ändrar en offert som redan är skickad.

**Visa PDF**, **Skriv ut** eller **Mejla till kund**. Vid utskick bifogas PDF:en, sparas bland
kundens dokument och journalförs med belopp och giltighetstid. Blir besöket kund flyttas offerten
med automatiskt.

En accepterad offert har knappen **Skapa arbetsorder**, som tar med alla rader. Då skrivs inget in
två gånger.

### Arbetsorder och fakturering

Fyll på med det som faktiskt gick åt, också sådant som tillkom under jobbet. Material och arbete
särredovisas.

Statusen driver allt annat:

| Status | Vad som händer |
|---|---|
| Öppen | Under arbete |
| Utförd | Materialet dras från lagret, en gång. Utfört-datum sätts |
| Fakturerad | Fakturadatum sätts, fakturanummer kan fyllas i |
| Betald | Betaldatum sätts |

En fakturerad order går inte att lägga rader på eller radera, bara makulera. Vyn **Fakturera**
visar allt som är utfört men inte fakturerat, och allt som är fakturerat men inte betalt, med
belopp. Det är där saker annars glöms bort.

Företagsuppgifterna som står överst på varje PDF fylls i under **Inställningar → Företag**.

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

## Platsbesök före kund

Ett besök är inte en kund. Den som ringer och vill ha ett pris blir ett **platsbesök** med bara det
som behövs för att åka dit: kontaktperson, fastighet, ärende, koordinat. Ingen kundpost, inget
kundnummer, ingen anläggning.

Blir det affär trycker du **Blev kund**. Då skapas kund och första anläggningen av det som redan är
ifyllt, koordinaten följer med, och en journalrad noterar vilket besök kunden kom ur, inklusive
offertsumman. Blir det inget sätter du status *Blev inget* med en anledning, och registret förblir
rent från folk som aldrig blev kunder.

| Status | Betyder |
|---|---|
| Inbokat | Besök planerat |
| Besökt | Varit där, inget pris lämnat än |
| Offert lämnad | Pris ute, väntar svar |
| Blev kund | Omvandlat, kunden finns i registret |
| Blev inget | Avslutat utan affär |

## Inför besöket: vad grannarna stötte på

Öppnar du ett besök med koordinat visas ett underlag byggt på SGU:s brunnsarkiv:

* hur djupt till berg grannarna hade, som spann och median
* hur djupt de borrade, uppdelat på vatten- och energibrunnar
* vilken kapacitet de fick, och hur stor andel som ligger under 600 l/h
* grundvattennivå, samt en lista på de närmaste brunnarna med avstånd

Överst står en tolkning i klartext: ungefär hur mycket foderrör som lär gå åt och vilket borrdjup
som är rimligt att räkna med. Radien går att ändra mellan 500 m och 5 km. Samma underlag finns på
befintliga anläggningar under **Grannbrunnar**.

**Vattenkvalitet går inte att få.** Brunnsarkivet innehåller läge, djup, jorddjup, foderrör,
kapacitet och nivåer, men ingen kemi och inga bakterier. SGU har separata data om
grundvattenkvalitet, men de kommer från miljöövervakningens stationer, inte från grannens brunn,
och säger ingenting om en enskild fastighet. Ett vattenprov på plats är enda vägen, och det står
också i underlaget så att ingen tror något annat.

### När underlaget är tomt

Tre olika saker kan ligga bakom att inga grannbrunnar visas, och de kräver olika åtgärder:

| Vad appen säger | Vad du gör |
|---|---|
| Ingen SGU-data är hämtad än | Inställningar → SGU, kryssa i ert län |
| Trakten verkar inte vara hämtad | Hämta länet där jobbet ligger |
| Inga registrerade brunnar inom radien | Ingenting, trakten är oborrad |
| Koordinaten hämtas | Vänta, underlaget kommer av sig självt |

Strår det att trakten inte verkar vara hämtad visas också hur långt bort närmaste nedladdade
brunn ligger. Är det tiotals mil har fel län hämtats.

### Hämta SGU-data

**Inställningar → SGU**: kryssa i de län ni jobbar i, spara. Sedan sköter appen resten. Saknade län
hämtas vid nästa uppstart och inom ett dygn, och de hålls uppdaterade när lokala data blivit äldre
än inställt antal dagar. SGU uppdaterar sina öppna data en gång i veckan, så sju dagar är rimligt.
**Hämta valda nu** finns kvar för den som inte vill vänta.

Data hämtas från SGU:s bulkfiler per län, en fil per anrop. Det paginerade JSON-API:et användes
först, men det svarar 404 utan formatparameter och kräver sidhantering som kan tappa poster.
Bulkfilerna är teckenkodade i cp1252 och har kolumner som läses efter namn, inte position, så en
ändrad kolumnordning hos SGU inte tyst förskjuter alla värden.

Brunnar som saknar koordinat i SGU:s register hoppas över, de går inte att placera. Hur många som
föll bort visas efter hämtningen. Datumen normaliseras: SGU skriver dem som `20120427`, `199307`
eller bara `1963`.

Licensen är Creative Commons Erkännande 4.0, vilket kräver att SGU anges som källa där uppgifterna
visas. Det sker automatiskt i underlaget.

Lägesnoggrannheten varierar. Många brunnar är satta på fastighetens mittpunkt snarare än på hålet,
vilket SGU själva påpekar. Underlaget duger utmärkt för att bedöma en trakt, inte för att peka ut
exakt var någon annans hål sitter.

## Dela med externa borrare

**Dela**-knappen finns på en anläggning och på ett platsbesök. Du väljer mottagare och kryssar i
vad som ska följa med. Valen skiljer sig åt:

| | Platsbesök | Anläggning |
|---|---|---|
| Fastighet, adress, koordinat, planerat besök | ✓ | ✓ |
| Kontaktperson och telefon | ✓ | ✓ |
| Vad ärendet gäller | ✓ | |
| Anteckningar från platsen / åtkomst | ✓ | ✓ |
| Borrdata, berg, pump | | ✓ |
| Grannbrunnar från SGU | ✓ | ✓ |

Ett besök har inga borrdata, hålet är ju inte borrat än. De fälten erbjuds därför inte, och skickas
de ändå via API:et avvisas de med ett begripligt fel i stället för att tyst ge tomma rader.
Offertsumman delas aldrig, den är intern.
Meddelandet skickas som e-post från din egen server via samma SMTP som påminnelserna.

Ingenting öppnas utåt. Appen ansluter bara ut, precis som förut. Det som skickas loggas i kundens
journal och i **Inställningar → SGU** under senast delat, så det går att svara på vem som fick vad.

Att ta emot borrprotokoll från externa borrare är inte byggt. När det blir aktuellt är den säkra
vägen samma princip: en egen brevlåda som appen hämtar från utgående via IMAP, avsändare på
godkänd lista, och allt som kommer in hamnar i en granskningskö där en människa godkänner innan
något skrivs till registret.

## Vem får vilken påminnelse

Varje påminnelse har en ägare. Den sätts automatiskt utifrån vem som gjorde jobbet:

| Påminnelse | Går till |
|---|---|
| Service, vattenprov, intyg | Den som senast skrev i journalen på anläggningen |
| Obetald faktura | Den som skapade arbetsordern |
| Offert utan besked | Den som skrev offerten |
| Besök utan återkoppling | Den som lade upp besöket |
| Egen påminnelse | Den som skapade den |

Den som var där sist vet mest om vad som behöver göras, och känner igen kunden.

I påminnelsevyn växlar du mellan **Mina** och **Allas**. Mina visar dina egna plus sådana ingen
tagit ansvar för, så att inget blir liggande för att det saknar ägare.

Under **Mitt konto** väljer var och en vad notiserna ska omfatta:

| Val | Betyder |
|---|---|
| Mina egna | Det jag ansvarar för, plus herrelösa |
| Allas | Allt som händer i företaget |
| Inga | Notiser avstängda, påminnelserna finns kvar i appen |

Administratörer står på **Allas** från början, så att inget faller mellan stolarna när någon är
sjuk eller slutar. Det går att ändra själv.

E-posten följer samma indelning: var och en får ett mejl med sina egna rader till adressen pa
kontot. Påminnelser utan ägare går till den gemensamma listan under Inställningar, sa att de inte
försvinner.

## Jobb i närheten

Både anläggningar som behöver något **och inbokade platsbesök** visas. Öppnar du ett besök står
under **Slå ihop med resan** vad mer som ligger inom tre mil: andra besök som är inbokade eller
väntar på svar, och anläggningar med förfallen service. Ett besök som passerat sitt datum hamnar
högst upp.

Besök som blivit kund eller lagts ner faller bort automatiskt. I närhetsvyn går de att stänga av
med **Inbokade besök → Bara anläggningar**.

Två situationer, samma underlag:

* **Du står någonstans.** Tryck på plats-ikonen i toppfältet, sedan *Använd min position*.
  Anläggningar inom vald radie listas med avstånd, riktning och varför de dyker upp.
* **Du planerar en resa.** Öppna kunden du ska till. Längst ned visas *Slå ihop med resan* med
  det som ligger inom tre mil från den anläggningen.

Sorteringen sätter angelägenhet före avstånd. En försenad service två mil bort hamnar före en
fungerande brunn på samma gata, eftersom det är den avstickaren som faktiskt är värd något.
Bocka i stoppen och tryck *Öppna rundan i kartan*, så byggs en Google Maps-rutt med din position
som start och stoppen som delmål (högst tio, det är kartans gräns).

### Fastighetsbeteckningar och adresser är inte samma sak

OpenStreetMap känner inte till svenska fastighetsbeteckningar. "Hasselmusen 2" finns inte i
deras register, men det kan finnas en plats med liknande namn någon helt annanstans i landet.
Därför gäller:

* **Beteckning utan kommun slås inte upp alls.** Samma beteckning finns i flera kommuner, och en
  sökning över hela landet ger inte en osäker träff utan en felaktig som ser riktig ut.
* **Beteckning med kommun** ger kommunens läge, markerat **ungefärlig**. Det duger för att se
  vilken trakt det gäller, inte för att peka ut tomten.
* **Träffar i fel kommun förkastas.** Ligger svaret utanför den kommun du angett är det fel
  plats, hur bra namnet än matchar, och appen går vidare till nästa formulering.

En ungefärlig koordinat märks ut på besöket och hamnar i systemhändelserna, så att ingen tror
att den pekar på borrplatsen. **Hämta min position** på plats är alltid det exakta alternativet.

För att få exakta lägen ur en fastighetsbeteckning krävs Lantmäteriets register, som är en
betaltjänst. Det går att koppla in senare om behovet finns.

### Koordinater hämtas av sig själva

Skriver du en adress eller fastighetsbeteckning slår servern upp koordinaten automatiskt, när ett
platsbesök skapas, när adressen ändras, och när en anläggning läggs till på en kund. Du behöver
inte trycka på något. Det betyder också att underlaget om grannbrunnar finns direkt när du öppnar
besöket.

Hittas ingen adress sparas posten ändå, utan koordinat och utan gissning. Tre vägar finns kvar:

* **Hämta min position** när du står på plats, det exaktaste alternativet
* **Slå upp adressen igen** efter att du rättat adressen
* skriv koordinaten för hand, i decimalgrader eller SWEREF 99 TM

En koordinat du satt själv skrivs aldrig över av ett automatiskt uppslag.

Uppslaget trappar ner tills något träffar: adress med kommun, adress utan kommun, adressen utan
husnummer, och till sist bara kommunen. Landar det på kommunnivå sägs det rakt ut att träffen är
ungefärlig, så att ingen tror att den pekar på tomten.

**Sätt `GEOCODER_USER_AGENT` i `.env`** till något som identifierar er, gärna med en kontaktadress.
OpenStreetMaps villkor kräver det, och utan det riskerar uppslagen att avvisas. Appen säger till
med just den instruktionen om tjänsten svarar 403.

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

### Mitt konto

Klicka på ditt namn uppe till höger. Där sköter varje användare, oavsett roll, sitt eget:
tvåfaktor, byte av lösenord, textstorlek, notiser och installation på hemskärmen. Tidigare låg
tvåfaktor under administrationen, dit bara administratörer kommer, vilket gjorde att en tekniker
inte kunde skydda sitt eget konto.

### Tvåfaktor

Under **Mitt konto** kan varje användare slå på tvåfaktor för sitt konto.
Servern visar en QR-kod att skanna med Google Authenticator, Aegis, 1Password eller liknande, samt
nyckeln i klartext om kameran krånglar. Inget slås på förrän du bekräftat med en giltig kod, så du
kan inte låsa ute dig själv genom att avbryta.

Att stänga av kräver lösenordet, annars räcker en obevakad skärm för att ta bort skyddet.

En administratör kan under **Inställningar → Konton** kräva tvåfaktor för en enskild användare
eller för alla. Den som omfattas möts av uppsättningen vid nästa anrop och kommer inte vidare
förrän den är klar: servern nekar allt utom kontosidan och själva uppsättningen. Påtvingad
tvåfaktor går inte att stänga av från kontosidan.

Undantaget är `/api/security`, som en administratör alltid når. Utan den nödutgången skulle en
administratör som slår på kravet utan att själv ha tvåfaktor låsa ut sig från inställningen som
skulle stänga av det igen. Rutten är fortfarande skyddad av adminrollen.

Tappar någon sin telefon nollställer en administratör tvåfaktor på kontot under **Hantera**.

### Roller

Under **Inställningar → Konton** kan en administratör byta användarnamn, sätta nytt lösenord,
kräva tvåfaktor, nollställa tvåfaktor och stänga av ett konto. En administratör kan varken stänga
av sitt eget konto eller ta bort sin egen adminroll, så det går inte att låsa ut sig av misstag.

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
| POST | `/api/new-facility` | Skapar kund + anläggning + första journalraden i ett anrop |
| GET/POST | `/api/visits` | Platsbesök före kund |
| POST | `/api/visits/{id}/convert` | Gör kund och anläggning av ett besök |
| GET | `/api/sgu/briefing` | Grannbrunnar och statistik inför besök |
| POST | `/api/sgu/sync` | Hämtar ett län från SGU, endast admin |
| POST | `/api/share` | Skickar valda uppgifter till extern borrare med e-post |
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

## Uppdateringar och cachning

Gränssnittet jämför sin version med serverns och lägger upp en list om de skiljer sig, med en
knapp som hämtar om allt. Knappen rensar service workerns cache, avregistrerar den och laddar om
på en ny adress.

Bakom listen ligger orsaken: webbläsare cachar JavaScript utan att alltid fråga servern. Därför
stämplas alla tillgångar med versionen, `/static/app.js?v=2.5.0`, så att en ny version blir en ny
adress som måste hämtas. `index.html` och `sw.js` levereras med `no-cache`, versionsstämplade
filer med lång hållbarhet. Ser du listen efter en uppdatering betyder det numera att webbläsaren
har en gammal flik öppen, inte att backend är gammal.

## När något går fel

**Mer, Systemhändelser.** Bakgrundsjobb har ingen användare att svara. Ett misslyckat
adressuppslag, en SGU-hämtning som inte gick igenom eller ett oväntat serverfel hamnar där, med
tidpunkt, källa och hela detaljen utfälld vid behov. Antalet ohanterade visas i Mer-listan.

Oväntade serverfel får ett referensnummer som både visas för användaren och skrivs i loggen:

```bash
docker compose logs app | grep <referens>
```

**Adressuppslag blockerar aldrig.** Ett besök sparas på någon hundradels sekund. Koordinaten
hämtas efteråt och skrivs pa posten när den kommit. Misslyckas det står det på besöket vad som
hände och vad du kan göra i stället, och händelsen hamnar i listan. Tidigare kunde ett uppslag
mot en trög adresstjänst ta upp emot en minut, och då hann proxyn framför appen ge upp.

## Tid och tidszon

Allt lagras i UTC och serveras med tidszon, så webbläsaren räknar om till lokal tid. Utan offset
i tidsstämpeln tolkar webbläsaren den som lokal tid, och en journalrad skriven 22:01 svensk tid
skulle visas som 20:01. Det gäller särskilt SQLite, som lagrar tidsstämplar utan zon.

### Löpnummer

Kundnummer, besöksnummer, offertnummer och de andra serierna utgår från det högsta nummer som
finns, inte från antalet rader. Räkningen gav krockar: raderade man en post sjönk antalet och
nästa post fick ett nummer som redan var taget. Ett raderat nummer återanvänds inte heller, så
att BES-1004 i någons anteckningar alltid betyder samma besök.

Två sparningar samtidigt kunde ändå läsa samma högsta nummer innan någon hunnit skriva, och
ett dubbelklick räckte för att utlösa det. Tilldelning och skrivning sker därför i ett odelbart
steg, och blir numret ändå upptaget tas nästa lediga automatiskt. Sparaknapparna spärras också
medan anropet pågår, så att ett dubbelklick blir en sparning och inte två.

## Uppdatera

```bash
cd /sokvag/till/borrjournal
./uppdatera.sh
```

Skriptet kontrollerar `.env`, tar en kopia av datavolymen, bygger om, väntar in appen och
jämför sedan backend mot gränssnitt. Skiljer de sig säger det till med en gång i stället för
att felet dyker upp hos en användare.

### Tre fällor värda att känna till

**1. Gränssnittet byggs inte in i imagen.** Katalogen `frontend` monteras in från disken:

```yaml
    volumes:
      - ./frontend:/app/frontend:ro
```

Byter du bara backendfilerna får du en ny server med gammalt gränssnitt, hur många gånger du än
kör `--no-cache`. Byt alltid ut båda. Servern läser numera gränssnittets version från disk, så
`/api/version` svarar med båda och `in_sync` säger om de stämmer. Versionen står också längst ned
under **Mer**.

**2. Radera aldrig projektkatalogen.** Där ligger `.env`, som inte följer med i git. Försvinner
den skapas en ny `SECRET_KEY`, och då blir alla utloggade eftersom deras inloggningar signerats
med den gamla.

**3. Katalognamnet styr vilken datavolym som används.** Volymen heter `<katalognamn>_data`. Ligger
projektet i `borrjournal` används `borrjournal_data`. Klonar du om till `borrjournal-ny` får du en
tom volym, och det ser ut som att all data är borta fast den ligger kvar i den gamla. Kontrollera
med `docker volume ls`.

### Vad datan ligger i

| Vad | Var | Överlever |
|---|---|---|
| Databas, filer, backuper | Docker-volymen `<katalog>_data` | ja, så länge katalognamnet är detsamma |
| Hemligheter | `.env` i projektkatalogen | bara om du inte raderar katalogen |
| Backend | inbyggt i imagen | byggs om varje gång |
| Gränssnitt | `./frontend` på disken | måste bytas ut för hand |

### Om du hämtar koden från git

Leveranserna kommer som zip. Klonar du från ditt eget repo måste du först lägga in den nya koden
där, annars hämtar `git clone` tillbaka den gamla versionen och ingenting ändras trots att allt
byggts om. Ett flöde som fungerar:

```bash
cd /sokvag/till/borrjournal
unzip -o ~/borrjournal.zip -d /tmp/ny
rsync -a --delete --exclude .env --exclude .git /tmp/ny/borrjournal/ ./
git add -A && git commit -m "version x.y.z" && git push
./uppdatera.sh
```

`--exclude .env` är det viktiga: annars skrivs hemligheterna över.

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
