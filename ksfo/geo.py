"""Geodesy helpers (stdlib only).

Local frame: an azimuthal equidistant projection on the WGS-84 ellipsoid,
centred on the KSFO airport reference point (ARP). For a point P we solve the
geodesic ARP -> P (Vincenty inverse) giving distance s (nm) and initial true
azimuth a, and set

    x = s * sin(a)   (nm, east)
    y = s * cos(a)   (nm, north)

Properties:
  * distance and true bearing *from the ARP* are exact (ellipsoidal);
  * distances between two other points are stretched across the radial by
    about (rho/sin rho - 1) ~ rho^2/6 with rho = s / 3440 nm: 1e-6 at 10 nm,
    5e-5 at 60 nm (0.003 nm over 60 nm), 6e-4 at 200 nm;
  * the frame's +y axis is true north *at the ARP*. Away from it, local true
    north is rotated from +y by the meridian convergence, roughly
    dlon * sin(lat): ~0.8 deg at 60 nm east/west of the field, ~2.7 deg at
    200 nm. A true course flown far east/west of the field therefore looks a
    little rotated in x/y; use the lat/lon if that matters.
"""
import math

A = 6378137.0                 # WGS-84 semi-major axis, m
F = 1 / 298.257223563
B = A * (1 - F)
M_PER_NM = 1852.0
FT_PER_NM = 6076.11549


def inverse(lat1, lon1, lat2, lon2):
    """Vincenty inverse. Returns (distance_nm, initial_true_az_deg, final_az_deg)."""
    if abs(lat1 - lat2) < 1e-12 and abs(lon1 - lon2) < 1e-12:
        return 0.0, 0.0, 0.0
    L = math.radians(lon2 - lon1)
    U1 = math.atan((1 - F) * math.tan(math.radians(lat1)))
    U2 = math.atan((1 - F) * math.tan(math.radians(lat2)))
    sU1, cU1, sU2, cU2 = math.sin(U1), math.cos(U1), math.sin(U2), math.cos(U2)
    lam = L
    for _ in range(200):
        sl, cl = math.sin(lam), math.cos(lam)
        ss = math.hypot(cU2 * sl, cU1 * sU2 - sU1 * cU2 * cl)
        if ss == 0:
            return 0.0, 0.0, 0.0
        cs = sU1 * sU2 + cU1 * cU2 * cl
        sig = math.atan2(ss, cs)
        sa = cU1 * cU2 * sl / ss
        c2a = 1 - sa * sa
        c2sm = cs - 2 * sU1 * sU2 / c2a if c2a else 0.0
        C = F / 16 * c2a * (4 + F * (4 - 3 * c2a))
        prev = lam
        lam = L + (1 - C) * F * sa * (sig + C * ss * (c2sm + C * cs * (-1 + 2 * c2sm * c2sm)))
        if abs(lam - prev) < 1e-12:
            break
    u2 = c2a * (A * A - B * B) / (B * B)
    Ak = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    Bk = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    ds = Bk * ss * (c2sm + Bk / 4 * (cs * (-1 + 2 * c2sm * c2sm) - Bk / 6 * c2sm * (-3 + 4 * ss * ss) * (-3 + 4 * c2sm * c2sm)))
    s = B * Ak * (sig - ds)
    a1 = math.degrees(math.atan2(cU2 * math.sin(lam), cU1 * sU2 - sU1 * cU2 * math.cos(lam))) % 360
    a2 = math.degrees(math.atan2(cU1 * math.sin(lam), -sU1 * cU2 + cU1 * sU2 * math.cos(lam))) % 360
    return s / M_PER_NM, a1, a2


def direct(lat1, lon1, az_deg, dist_nm):
    """Vincenty direct. Returns (lat, lon)."""
    s = dist_nm * M_PER_NM
    a1 = math.radians(az_deg)
    U1 = math.atan((1 - F) * math.tan(math.radians(lat1)))
    sig1 = math.atan2(math.tan(U1), math.cos(a1))
    sa = math.cos(U1) * math.sin(a1)
    c2a = 1 - sa * sa
    u2 = c2a * (A * A - B * B) / (B * B)
    Ak = 1 + u2 / 16384 * (4096 + u2 * (-768 + u2 * (320 - 175 * u2)))
    Bk = u2 / 1024 * (256 + u2 * (-128 + u2 * (74 - 47 * u2)))
    sig = s / (B * Ak)
    for _ in range(200):
        c2sm = math.cos(2 * sig1 + sig)
        ss, cs = math.sin(sig), math.cos(sig)
        ds = Bk * ss * (c2sm + Bk / 4 * (cs * (-1 + 2 * c2sm * c2sm) - Bk / 6 * c2sm * (-3 + 4 * ss * ss) * (-3 + 4 * c2sm * c2sm)))
        prev = sig
        sig = s / (B * Ak) + ds
        if abs(sig - prev) < 1e-12:
            break
    ss, cs = math.sin(sig), math.cos(sig)
    c2sm = math.cos(2 * sig1 + sig)
    sU1, cU1 = math.sin(U1), math.cos(U1)
    lat2 = math.atan2(sU1 * cs + cU1 * ss * math.cos(a1),
                      (1 - F) * math.hypot(sa, sU1 * ss - cU1 * cs * math.cos(a1)))
    lam = math.atan2(ss * math.sin(a1), cU1 * cs - sU1 * ss * math.cos(a1))
    C = F / 16 * c2a * (4 + F * (4 - 3 * c2a))
    L = lam - (1 - C) * F * sa * (sig + C * ss * (c2sm + C * cs * (-1 + 2 * c2sm * c2sm)))
    return math.degrees(lat2), lon1 + math.degrees(L)


class LocalFrame:
    """Azimuthal equidistant frame (nm, x east, y north) centred on (lat0, lon0)."""

    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0

    def to_xy(self, lat, lon):
        s, az, _ = inverse(self.lat0, self.lon0, lat, lon)
        a = math.radians(az)
        return s * math.sin(a), s * math.cos(a)

    def to_latlon(self, x, y):
        s = math.hypot(x, y)
        if s == 0:
            return self.lat0, self.lon0
        return direct(self.lat0, self.lon0, math.degrees(math.atan2(x, y)) % 360, s)


def dms(text):
    """Parse ARINC 424 lat/lon: N37370770 / W122223150 (hundredths of seconds),
    or the 11/12-char high-precision form (ten-thousandths) used in path points."""
    text = text.strip()
    if not text:
        return None
    hemi, body = text[0], text[1:]
    deg_len = 2 if hemi in "NS" else 3
    d = int(body[:deg_len])
    m = int(body[deg_len:deg_len + 2])
    frac = body[deg_len + 2:]
    sec = int(frac[:2]) + int(frac[2:] or 0) / (10 ** len(frac[2:]))
    val = d + m / 60 + sec / 3600
    return -val if hemi in "SW" else val
