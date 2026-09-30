import os
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()  # загружает переменные из файла .env

# Получаем параметры БД из переменных окружения
dbname = os.getenv("DB_NAME")
db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")

if not all([dbname, db_user, db_password]):
    raise ValueError(
        "Не заданы переменные окружения DB_NAME, DB_USER, DB_PASSWORD. "
        "Проверьте файл .env и что он лежит в папке запуска скрипта."
    )


def get_country_bbox(country_name: str) -> Optional[Dict[str, Any]]:
    """
    Получает географические координаты и bounding box страны через Nominatim API.
    """
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": country_name, "format": "json", "limit": 1}
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": "Coursework/1.0"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    if not data:
        return None
    bbox = list(map(float, data[0]["boundingbox"]))
    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    return {
        "name": country_name,
        "lat": lat,
        "lon": lon,
        "bbox_min_lat": bbox[0],
        "bbox_max_lat": bbox[1],
        "bbox_min_lon": bbox[2],
        "bbox_max_lon": bbox[3],
    }


def get_aeroplanes_in_bbox(bbox: Dict[str, float]) -> List[List[Any]]:
    """
    Получает список самолётов в заданном bounding box через OpenSky API.
    """
    url = "https://opensky-network.org/api/states/all"
    params = {
        "lamin": bbox["bbox_min_lat"],
        "lamax": bbox["bbox_max_lat"],
        "lomin": bbox["bbox_min_lon"],
        "lomax": bbox["bbox_max_lon"],
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    if not data or "states" not in data or not data["states"]:
        return []
    return data["states"]


class DBManager:
    def __init__(self, dbname: str, user: str, password: str, host: str = "localhost", port: int = 5432) -> None:
        self.conn = psycopg2.connect(
            dbname=dbname,
            user=user,
            password=password,
            host=host,
            port=port,
        )
        self.cur = self.conn.cursor()

    def insert_countries(self, countries: List[Dict[str, Any]]) -> None:
        sql = """
            INSERT INTO countries (name, lat, lon, bbox_min_lat, bbox_max_lat,
                                   bbox_min_lon, bbox_max_lon)
            VALUES %s
            ON CONFLICT (name) DO NOTHING
        """
        values = [
            (c["name"], c["lat"], c["lon"], c["bbox_min_lat"], c["bbox_max_lat"], c["bbox_min_lon"], c["bbox_max_lon"])
            for c in countries
        ]
        execute_values(self.cur, sql, values)
        self.conn.commit()

    def insert_aeroplanes(self, aeroplanes: List[List[Any]], country_id: int) -> None:
        sql = """
                    INSERT INTO aeroplanes (
                        icao24, callsign, origin_country, time_position, last_contact,
                        longitude, latitude, baro_altitude, on_ground, velocity,
                        true_track, vertical_rate, squawk, spi, country_id
                    ) VALUES %s
                    ON CONFLICT (icao24) DO UPDATE SET
                        callsign = EXCLUDED.callsign,
                        velocity = EXCLUDED.velocity,
                        last_contact = EXCLUDED.last_contact,
                        longitude = EXCLUDED.longitude,
                        latitude = EXCLUDED.latitude
                """
        values = []
        for a in aeroplanes:
            # a[13] — это поле spi из OpenSky (приходит как 0/1)
            spi_bool = bool(a[13]) if a[13] is not None else None

            values.append(
                (a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], spi_bool, country_id)
            )

        execute_values(self.cur, sql, values)
        self.conn.commit()

    def get_countries_and_aeroplanes_count(self) -> List[Tuple[str, int]]:
        sql = """
            SELECT c.name, COUNT(a.icao24) AS total_aeroplanes
            FROM countries c
            LEFT JOIN aeroplanes a ON c.id = a.country_id
            GROUP BY c.id, c.name
        """
        self.cur.execute(sql)
        return self.cur.fetchall()

    def get_all_aeroplanes(self) -> List[Tuple[Any, ...]]:
        sql = "SELECT * FROM aeroplanes"
        self.cur.execute(sql)
        return self.cur.fetchall()

    def get_avg_speed(self) -> Optional[float]:
        sql = "SELECT AVG(velocity) FROM aeroplanes WHERE velocity IS NOT NULL"
        self.cur.execute(sql)
        row = self.cur.fetchone()
        return row[0] if row else None

    def get_aeroplanes_with_higher_speed(self) -> List[Tuple[Any, ...]]:
        avg_speed = self.get_avg_speed()
        if avg_speed is None:
            return []
        sql = "SELECT * FROM aeroplanes WHERE velocity > %s"
        self.cur.execute(sql, (avg_speed,))
        return self.cur.fetchall()

    def get_aeroplanes_with_keyword(self, keyword: str) -> List[Tuple[Any, ...]]:
        sql = "SELECT * FROM aeroplanes WHERE callsign ILIKE %s"
        self.cur.execute(sql, (f"%{keyword}%",))
        return self.cur.fetchall()

    def close(self) -> None:
        self.cur.close()
        self.conn.close()


if __name__ == "__main__":
    countries_list = [
        "Russia",
        "United States",
        "China",
        "India",
        "Brazil",
        "Canada",
        "Australia",
        "France",
        "Germany",
        "Japan",
    ]
    country_data = [get_country_bbox(c) for c in countries_list]
    country_data = [c for c in country_data if c]

    db = DBManager(dbname=dbname, user=db_user, password=db_password)
    db.insert_countries(country_data)

    db.cur.execute("SELECT id, name FROM countries")
    country_map = {row[1]: row[0] for row in db.cur.fetchall()}

    for country in country_data:
        aeroplanes = get_aeroplanes_in_bbox(country)
        if aeroplanes:
            db.insert_aeroplanes(aeroplanes, country_map[country["name"]])

    print(db.get_countries_and_aeroplanes_count())
    print(db.get_avg_speed())
    print(db.get_aeroplanes_with_higher_speed())
    print(db.get_aeroplanes_with_keyword("ACA"))

    db.close()


# black main.py
# flake8 main.py
# mypy main.py
# isort main.py
