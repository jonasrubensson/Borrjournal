"""Versionsnummer. Höjs när API:et ändras.

Gränssnittet jämför sitt eget nummer med serverns och varnar om de går isär.
Det händer lätt: frontend-katalogen är monterad live i containern medan
backend-koden bakas in i imagen, så en uppdatering utan --build ger nytt
gränssnitt mot gammalt API.
"""

APP_VERSION = "3.5.0"
