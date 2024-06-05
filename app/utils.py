import math


R = EARTH_RADIUS_KM = 6371


def calc_haversine_distance(
        point1: tuple[float, float],
        point2: tuple[float, float]) -> float:

    """
    Calculates the great-circle distance between two points on a sphere
    given their longitudes and latitudes
    """

    lat1 = math.radians(point1[0])
    lat2 = math.radians(point2[0])

    lat_diff = lat2 - lat1
    lon_diff = math.radians(point2[1] - point1[1])

    d = 2 * R * math.asin(
        math.sqrt(
            math.sin(lat_diff / 2) * math.sin(lat_diff / 2) +
            math.cos(lat1) * math.cos(lat2) * math.sin(lon_diff / 2) * math.sin(lon_diff / 2)
        )
    )

    return d


def calc_destination_point_by(point1: tuple[float, float], d: float, bearing: float) -> tuple[float, float]:
    """Calculates destination point given distance and bearing from start point"""
    lat, lon = point1

    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    theta = math.radians(bearing)

    lat2 = math.asin(
        math.sin(lat1) * math.cos(d / R) +
        math.cos(lat1) * math.sin(d / R) * math.cos(theta)
    )
    lon2 = lon1 + math.atan2(
        math.sin(theta) * math.sin(d / R) * math.cos(lat1),
        math.cos(d / R) - math.sin(lat1) * math.sin(lat2)
    )

    return (
        math.degrees(lat2),
        ((math.degrees(lon2) + 540) % 360 - 180)
    )
