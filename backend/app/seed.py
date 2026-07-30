from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from .models import Customer, Facility, JournalEntry

# Flera kunder delar pumpmodell med flit, så flottfiltret blir meningsfullt att testa.
DEMO = [
    dict(
        name="Erik & Maja Lundqvist", customer_type="Privat", phone="070-441 20 18",
        email="erik.lundqvist@example.se", property_designation="Vässlan 3:14",
        municipality="Norrtälje", address="Vässlanvägen 12",
        facilities=[dict(
            facility_type="Bergborrad brunn", drilled_at="2021-05-04", soil_depth_m=6,
            casing_length_m=6.5, total_depth_m=72, water_level_m=14, capacity_lph=1400,
            bedrock_notes="Morän 0-6 m, granit 6-72 m. Vattenförande spricka vid 41 m.",
            pump_manufacturer="Grundfos", pump_model="SQ 2-70", pump_serial="GF-2021-88412",
            pump_depth_m=45, pump_status="Installerad", pressure_tank="Wilo 60 l",
            pump_installed_at="2021-05-14", last_service_at="2026-06-18",
        )],
        journal=[
            ("Service", "Årlig funktionskontroll", "Tryckkärl 3,2 bar, inga läckage. Filter bytt. Vattennivå 13,8 m.", 42),
            ("Installation", "Pump installerad och driftsatt", "Grundfos SQ 2-70 på 45 m. Provpumpning 2 h, stabilt 1400 l/h.", 1900),
        ],
    ),
    dict(
        name="Skogsbacken Lantbruk AB", customer_type="Företag", org_no="556677-8899",
        phone="0175-320 04", email="drift@skogsbacken.example",
        property_designation="Skogsbacken 1:3", municipality="Norrtälje",
        facilities=[
            dict(
                facility_type="Bergborrad brunn", drilled_at="2019-09-10", soil_depth_m=11,
                casing_length_m=12, total_depth_m=118, water_level_m=22, capacity_lph=3200,
                pump_manufacturer="Grundfos", pump_model="SP 3A-25", pump_serial="GF-2019-11204",
                pump_depth_m=80, pump_status="Installerad", pump_installed_at="2019-09-20",
                last_service_at="2026-04-11",
            ),
            dict(
                facility_type="Energibrunn", drilled_at="2020-09-28", soil_depth_m=9,
                casing_length_m=9.5, total_depth_m=165, water_level_m=26,
                pump_status="Ingen pump (energibrunn)", last_service_at="2025-10-02",
                service_interval_months=24,
            ),
        ],
        journal=[
            ("Service", "Servicebesök båda brunnarna", "B-1907: pumpen drar 8 % mer ström än vid installation, håll under uppsikt.", 110),
            ("Anmärkning", "Grumligt vatten efter kraftigt regn", "Misstänker inläckage vid brunnslock. Ny tätning beställd.", 900),
        ],
    ),
    dict(
        name="Karin Ahlgren", customer_type="Privat", phone="073-118 90 22",
        email="karin.ahlgren@example.se", property_designation="Ekhaga 2:7",
        municipality="Norrtälje",
        facilities=[dict(
            facility_type="Bergborrad brunn", drilled_at="2023-06-14", soil_depth_m=4,
            casing_length_m=6, total_depth_m=64, water_level_m=9, capacity_lph=600,
            bedrock_notes="Svag tillrinning, sprickzon saknas under 50 m.",
            pump_manufacturer="Lowara", pump_model="4GS03", pump_serial="LW-2023-4471",
            pump_depth_m=55, pump_status="Installerad", pump_installed_at="2023-06-30",
            last_service_at="2026-01-20", status="action",
        )],
        journal=[
            ("Telefon", "Vattenbrist, pump går torr", "Vattnet tar slut efter ca 20 min tappning. Akutbesök bokat.", 9),
            ("Service", "Torrkörningsskydd monterat", "Nivåvakt installerad på 55 m.", 191),
        ],
    ),
    dict(
        name="Grönviks Samfällighet", customer_type="Förening", org_no="717000-1122",
        phone="0176-500 12", email="styrelsen@gronvik.example",
        property_designation="Grönvik 1:1", municipality="Norrtälje",
        facilities=[dict(
            facility_type="Bergborrad brunn", drilled_at="2024-06-03", soil_depth_m=8,
            casing_length_m=9, total_depth_m=96, water_level_m=17, capacity_lph=2100,
            pump_manufacturer="Grundfos", pump_model="SP 5A-12", pump_serial="GF-2024-55301",
            pump_depth_m=70, pump_status="Installerad", pump_installed_at="2024-06-12",
            last_service_at="2026-06-12", service_interval_months=24,
        )],
        journal=[("Service", "Tvåårskontroll", "Allt inom normalvärden. Uttag mätt till 2050 l/h.", 48)],
    ),
    dict(
        name="Tor & Lisbeth Hedman", customer_type="Privat", phone="070-902 77 41",
        email="hedman@example.se", property_designation="Norrgården 4:2",
        municipality="Norrtälje",
        facilities=[dict(
            facility_type="Bergborrad brunn", drilled_at="2026-07-09", soil_depth_m=5,
            casing_length_m=6, total_depth_m=81, water_level_m=12, capacity_lph=1800,
            bedrock_notes="Vattenförande spricka vid 47 m, god tillrinning.",
            pump_status="Ska installeras", permit_status="Beviljad",
        )],
        journal=[("Borrning", "Borrning avslutad på 81 m", "Renblåsning 45 min. Väntar på pumpval innan installation.", 21)],
    ),
    dict(
        name="Anders Wikner", customer_type="Privat", phone="070-556 12 03",
        email="a.wikner@example.se", property_designation="Sjöhagen 1:9",
        municipality="Uppsala",
        facilities=[dict(
            facility_type="Bergborrad brunn", drilled_at="2022-04-19", soil_depth_m=7,
            casing_length_m=8, total_depth_m=88, water_level_m=16, capacity_lph=1500,
            pump_manufacturer="Grundfos", pump_model="SQ 2-70", pump_serial="GF-2022-90117",
            pump_depth_m=60, pump_status="Installerad", pump_installed_at="2022-05-02",
            last_service_at="2025-05-02",
        )],
        journal=[("Service", "Treårsservice", "Bytte tryckvakt. Pumpen låter normalt.", 455)],
    ),
    dict(
        name="Fjärdens Camping", customer_type="Företag", org_no="559911-2233",
        phone="0176-712 20", email="info@fjardenscamping.example",
        property_designation="Fjärden 2:41", municipality="Norrtälje",
        facilities=[dict(
            facility_type="Bergborrad brunn", drilled_at="2022-03-08", soil_depth_m=10,
            casing_length_m=11, total_depth_m=104, water_level_m=19, capacity_lph=4200,
            pump_manufacturer="Grundfos", pump_model="SQ 2-70", pump_serial="GF-2022-90455",
            pump_depth_m=75, pump_status="Installerad", pump_installed_at="2022-06-11",
            last_service_at="2026-03-05",
        )],
        journal=[("Service", "Kontroll inför säsong", "Klorering utförd. Vattenprov skickat till labb.", 147)],
    ),
]


async def seed_demo(db: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    cno, fno = 1041, 1901

    for row in DEMO:
        facilities = row.pop("facilities", [])
        entries = row.pop("journal", [])
        cno += 1
        customer = Customer(customer_no=f"K-{cno}", **row)
        db.add(customer)
        await db.flush()

        first_facility_id = None
        for spec in facilities:
            fno += 1
            facility = Facility(facility_no=f"B-{fno}", customer_id=customer.id, **spec)
            db.add(facility)
            await db.flush()
            first_facility_id = first_facility_id or facility.id

        for entry_type, title, body, days_ago in entries:
            db.add(
                JournalEntry(
                    customer_id=customer.id,
                    facility_id=first_facility_id,
                    entry_type=entry_type,
                    title=title,
                    body=body,
                    created_at=now - timedelta(days=days_ago),
                    author_name="Mikael B.",
                )
            )

    await db.commit()
