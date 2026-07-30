"""Koordinater och avstånd.

Systemet lagrar alltid WGS84 (lat/lon) som decimaltal, eftersom det är vad webbläsarens
GPS och kartlänkar använder. Men borrprotokoll och kommunens kartor anger ofta SWEREF 99 TM,
så inmatning i det formatet tolkas och räknas om.
"""

from __future__ import annotations

import math
import re

# Parametrar för SWEREF 99 TM (Gauss-Krüger på GRS 80)
_AXIS = 6378137.0
_FLATTENING = 1 / 298.257222101
_CENTRAL_MERIDIAN = 15.00
_SCALE = 0.9996
_FALSE_NORTHING = 0.0
_FALSE_EASTING = 500000.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Fågelvägen i kilometer. Räcker gott för att sortera jobb efter närhet."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def sweref99tm_to_wgs84(northing: float, easting: float) -> tuple[float, float]:
    e2 = _FLATTENING * (2 - _FLATTENING)
    n = _FLATTENING / (2 - _FLATTENING)
    a_hat = _AXIS / (1 + n) * (1 + n**2 / 4 + n**4 / 64)

    d1 = n / 2 - 2 * n**2 / 3 + 37 * n**3 / 96 - n**4 / 360
    d2 = n**2 / 48 + n**3 / 15 - 437 * n**4 / 1440
    d3 = 17 * n**3 / 480 - 37 * n**4 / 840
    d4 = 4397 * n**4 / 161280

    # Den omvända formeln har egna koefficienter, inte samma som den framåtriktade.
    a_star = e2 + e2**2 + e2**3 + e2**4
    b_star = -(7 * e2**2 + 17 * e2**3 + 30 * e2**4) / 6
    c_star = (224 * e2**3 + 889 * e2**4) / 120
    d_star = -(4279 * e2**4) / 1260

    xi = (northing - _FALSE_NORTHING) / (_SCALE * a_hat)
    eta = (easting - _FALSE_EASTING) / (_SCALE * a_hat)

    xi_prim = (
        xi
        - d1 * math.sin(2 * xi) * math.cosh(2 * eta)
        - d2 * math.sin(4 * xi) * math.cosh(4 * eta)
        - d3 * math.sin(6 * xi) * math.cosh(6 * eta)
        - d4 * math.sin(8 * xi) * math.cosh(8 * eta)
    )
    eta_prim = (
        eta
        - d1 * math.cos(2 * xi) * math.sinh(2 * eta)
        - d2 * math.cos(4 * xi) * math.sinh(4 * eta)
        - d3 * math.cos(6 * xi) * math.sinh(6 * eta)
        - d4 * math.cos(8 * xi) * math.sinh(8 * eta)
    )

    phi_star = math.asin(math.sin(xi_prim) / math.cosh(eta_prim))
    delta_lambda = math.atan(math.sinh(eta_prim) / math.cos(xi_prim))

    lon = _CENTRAL_MERIDIAN + math.degrees(delta_lambda)
    lat = math.degrees(
        phi_star
        + math.sin(phi_star)
        * math.cos(phi_star)
        * (
            a_star
            + b_star * math.sin(phi_star) ** 2
            + c_star * math.sin(phi_star) ** 4
            + d_star * math.sin(phi_star) ** 6
        )
    )
    return lat, lon


def wgs84_to_sweref99tm(lat: float, lon: float) -> tuple[float, float]:
    e2 = _FLATTENING * (2 - _FLATTENING)
    n = _FLATTENING / (2 - _FLATTENING)
    a_hat = _AXIS / (1 + n) * (1 + n**2 / 4 + n**4 / 64)

    a = e2
    b = (5 * e2**2 - e2**3) / 6
    c = (104 * e2**3 - 45 * e2**4) / 120
    d = 1237 * e2**4 / 1260

    b1 = n / 2 - 2 * n**2 / 3 + 5 * n**3 / 16 + 41 * n**4 / 180
    b2 = 13 * n**2 / 48 - 3 * n**3 / 5 + 557 * n**4 / 1440
    b3 = 61 * n**3 / 240 - 103 * n**4 / 140
    b4 = 49561 * n**4 / 161280

    phi = math.radians(lat)
    delta_lambda = math.radians(lon - _CENTRAL_MERIDIAN)

    phi_star = phi - math.sin(phi) * math.cos(phi) * (
        a + b * math.sin(phi) ** 2 + c * math.sin(phi) ** 4 + d * math.sin(phi) ** 6
    )
    xi_prim = math.atan(math.tan(phi_star) / math.cos(delta_lambda))
    eta_prim = math.atanh(math.cos(phi_star) * math.sin(delta_lambda))

    northing = _SCALE * a_hat * (
        xi_prim
        + b1 * math.sin(2 * xi_prim) * math.cosh(2 * eta_prim)
        + b2 * math.sin(4 * xi_prim) * math.cosh(4 * eta_prim)
        + b3 * math.sin(6 * xi_prim) * math.cosh(6 * eta_prim)
        + b4 * math.sin(8 * xi_prim) * math.cosh(8 * eta_prim)
    ) + _FALSE_NORTHING
    easting = _SCALE * a_hat * (
        eta_prim
        + b1 * math.cos(2 * xi_prim) * math.sinh(2 * eta_prim)
        + b2 * math.cos(4 * xi_prim) * math.sinh(4 * eta_prim)
        + b3 * math.cos(6 * xi_prim) * math.sinh(6 * eta_prim)
        + b4 * math.cos(8 * xi_prim) * math.sinh(8 * eta_prim)
    ) + _FALSE_EASTING
    return northing, easting


# Sverige, generöst tilltaget. Fångar siffror som hamnat i fel ordning eller fel system.
SWEDEN_LAT = (55.0, 69.2)
SWEDEN_LON = (10.5, 24.3)
SWEREF_N = (6100000, 7700000)
SWEREF_E = (200000, 950000)


def in_sweden(lat: float, lon: float) -> bool:
    return SWEDEN_LAT[0] <= lat <= SWEDEN_LAT[1] and SWEDEN_LON[0] <= lon <= SWEDEN_LON[1]


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """Tolkar det montören råkar klistra in.

    Klarar decimalgrader ('59.7231, 18.9412'), SWEREF 99 TM ('N 6620123 E 674321'),
    N/E-prefix i valfri ordning, komma som decimaltecken och graden med minuter
    ('59°43.4'N 18°56.5'E').
    """
    if not text:
        return None
    raw = text.strip()

    # Grader och minuter, t.ex. 59°43.386'N 18°56.472'E
    dm = re.findall(r"(\d+)\s*[°º]\s*([\d.,]+)\s*['′]?\s*([NSEWnsewÖöV])", raw)
    if len(dm) == 2:
        values = {}
        for deg, minutes, hemi in dm:
            value = int(deg) + float(minutes.replace(",", ".")) / 60
            key = "lat" if hemi.upper() in "NS" else "lon"
            if hemi.upper() in "SWV":
                value = -value
            values[key] = value
        if "lat" in values and "lon" in values:
            return values["lat"], values["lon"]

    cleaned = raw.replace("°", " ")
    numbers = re.findall(r"-?\d+(?:[.,]\d+)?", cleaned)
    if len(numbers) < 2:
        return None
    a = float(numbers[0].replace(",", "."))
    b = float(numbers[1].replace(",", "."))

    # Etiketter avgör ordningen om de finns
    upper = cleaned.upper()
    if re.search(r"\bE\b|\bÖ\b", upper) and re.search(r"\bN\b", upper):
        n_pos = upper.find("N")
        e_pos = max(upper.find("E"), upper.find("Ö"))
        if e_pos != -1 and n_pos != -1 and e_pos < n_pos:
            a, b = b, a

    # SWEREF 99 TM känns igen på storleksordningen
    if SWEREF_N[0] <= a <= SWEREF_N[1] and SWEREF_E[0] <= b <= SWEREF_E[1]:
        lat, lon = sweref99tm_to_wgs84(a, b)
        return (round(lat, 6), round(lon, 6)) if in_sweden(lat, lon) else None

    # Decimalgrader, eventuellt i omvänd ordning
    if in_sweden(a, b):
        return round(a, 6), round(b, 6)
    if in_sweden(b, a):
        return round(b, 6), round(a, 6)
    if -90 <= a <= 90 and -180 <= b <= 180 and (a, b) != (0.0, 0.0):
        return round(a, 6), round(b, 6)
    return None


def bearing_label(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Grov riktning, så att listan går att läsa utan karta."""
    dl = math.radians(lon2 - lon1)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    deg = (math.degrees(math.atan2(y, x)) + 360) % 360
    names = ["norr", "nordost", "öster", "sydost", "söder", "sydväst", "väster", "nordväst"]
    return names[int((deg + 22.5) // 45) % 8]
