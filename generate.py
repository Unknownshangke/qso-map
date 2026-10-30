#!/usr/bin/env python3
"""QSO dot-matrix world map for a QRZ.com bio page.

The map is a grid of dots: land is drawn as faint dots, every worked QSO lights
up one dot, and the newer the QSO the bigger/brighter the dot.

QRZ bios cannot run JavaScript, so the map is rendered to a static SVG/PNG and
included as <img>. Re-run this script (cron / GitHub Action) to refresh it.

Data sources
  --key   QRZ Logbook API key  (logbook.qrz.com/api, ACTION=FETCH)
  --adif  a local ADIF export  (QRZ Logbook -> Download log)
  --demo  synthetic data, for a quick look

Examples
  python generate.py --demo
  python generate.py --adif BA4RKS.adi --out .
  python generate.py --key $QRZ_KEY --out . --months 24
"""
import argparse
import datetime as dt
import json
import math
import os
import random
import re
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GEO = os.path.join(HERE, "geo")

# ---------------------------------------------------------------- style
PITCH = 9.0
PAD_X, PAD_TOP, PAD_BOTTOM = 16.0, 12.0, 38.0
LAND_R = 1.2
LAND_FILL = "#3c5877"
LAND_OPACITY = 0.9
DOT_FILL = "#2dd4bf"
DOT_FILL_NEW = "#7ff0e0"
CAPTION_FILL = "#7d91a8"
FONT = "'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,Menlo,Consolas,monospace"

R_NEW, R_OLD = 4.6, 2.2


# ---------------------------------------------------------------- adif
TAG_RE = re.compile(r"<([A-Za-z0-9_]+)(?::(\d+)(?::[A-Za-z])?)?>", re.I)


def parse_adif(text):
    i = text.upper().find("<EOH>")
    body = text[i + 5:] if i >= 0 else text
    recs, rec, pos = [], {}, 0
    while True:
        m = TAG_RE.search(body, pos)
        if not m:
            break
        tag, ln = m.group(1).upper(), m.group(2)
        pos = m.end()
        if ln:
            val = body[pos:pos + int(ln)]
            pos += int(ln)
        else:
            val = ""
        if tag == "EOR":
            if rec:
                recs.append(rec)
            rec = {}
        elif tag == "EOH":
            continue
        else:
            rec[tag] = val
    if rec:
        recs.append(rec)
    return recs


def maidenhead(loc):
    loc = (loc or "").strip().upper()
    if len(loc) < 4:
        return None
    try:
        lon = (ord(loc[0]) - 65) * 20 - 180
        lat = (ord(loc[1]) - 65) * 10 - 90
        lon += (int(loc[2])) * 2
        lat += (int(loc[3]))
        if len(loc) >= 6:
            lon += (ord(loc[4]) - 65) * 2 / 24 + 1 / 24
            lat += (ord(loc[5]) - 65) * 1 / 24 + 1 / 48
        else:
            lon += 1
            lat += 0.5
        return lat, lon
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------- geo
class Geo:
    def __init__(self):
        with open(os.path.join(GEO, "land_mask.json"), encoding="utf-8") as f:
            m = json.load(f)
        self.cols, self.rows = m["cols"], m["rows"]
        self.top, self.bottom = m["top"], m["bottom"]
        self.mask = m["mask"]
        with open(os.path.join(GEO, "country_centroids.json"), encoding="utf-8") as f:
            self.centroids = json.load(f)
        self._alias = {k.lower(): v for k, v in self.centroids.items()}
        self._alias.update({k.lower().replace("the ", ""): v for k, v in self.centroids.items()})

        extra = {
            "united states": "United States of America",
            "usa": "United States of America",
            "russia": "Russia",
            "uk": "United Kingdom",
            "england": "United Kingdom",
            "scotland": "United Kingdom",
            "wales": "United Kingdom",
            "northern ireland": "United Kingdom",
            "czech republic": "Czechia",
            "south korea": "South Korea",
            "korea": "South Korea",
            "republic of korea": "South Korea",
            "vietnam": "Vietnam",
            "laos": "Laos",
            "syria": "Syria",
            "iran": "Iran",
            "bolivia": "Bolivia",
            "venezuela": "Venezuela",
            "tanzania": "Tanzania",
            "moldova": "Moldova",
            "macedonia": "North Macedonia",
            "swaziland": "eSwatini",
            "ivory coast": "Cote d'Ivoire",
            "cote d'ivoire": "Cote d'Ivoire",
            "cape verde": "Cape Verde",
            "hawaii": "United States of America",
            "alaska": "United States of America",
            "puerto rico": "Puerto Rico",
            "federal republic of germany": "Germany",
            "fed. rep. of germany": "Germany",
            "german democratic republic": "Germany",
            "people's republic of china": "China",
            "republic of china": "Taiwan",
            "hong kong": "Hong Kong",
            "macau": "Macao",
        }
        self._extra_alias = extra

    def cell(self, lat, lon):
        c = int((lon + 180.0) / (360.0 / self.cols))
        r = int((self.top - lat) / ((self.top - self.bottom) / self.rows))
        if 0 <= c < self.cols and 0 <= r < self.rows:
            return c, r
        return None

    def country_latlon(self, country):
        if not country:
            return None
        key = country.strip().lower()
        if key in self._extra_alias:
            name = self._extra_alias[key]
            return self.centroids.get(name)
        if key in self._alias:
            return self._alias[key]
        for name, v in self.centroids.items():
            if key in name.lower():
                return v
        return None


# ---------------------------------------------------------------- data
UA = "BA4RKS-qso-map/1.0 (BA4RKS)"


def qrz_fetch(key, start=None, end=None, limit=5000, timeout=120):
    """Fetch ADIF from the QRZ Logbook API.

    Note: FETCH requires a QRZ subscription.  Without one, export the ADIF
    from the QRZ Logbook web page and use --adif instead.
    """
    opt = []
    if start and end:
        opt.append("BETWEEN:%s+%s" % (start, end))
    if limit:
        opt.append("MAX:%d" % limit)
    data = {"KEY": key, "ACTION": "FETCH"}
    if opt:
        data["OPTION"] = ",".join(opt)

    req = urllib.request.Request(
        "https://logbook.qrz.com/api",
        data=urllib.parse.urlencode(data).encode(),
        headers={"User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")

    if not raw.startswith("RESULT=OK"):
        m = re.search(r"REASON=([^&]*)", raw)
        reason = urllib.parse.unquote(m.group(1)).replace("+", " ") if m else raw[:200]
        raise SystemExit(
            "QRZ API error: %s\n"
            "FETCH needs a QRZ subscription.  Without it, export ADIF from the QRZ\n"
            "Logbook page (Download log) and run with --adif." % reason
        )
    m = re.search(r"(?:^|&)ADIF=(.*)$", raw, re.S)
    if not m:
        raise SystemExit("QRZ API: response has no ADIF (%s...)" % raw[:200])
    return urllib.parse.unquote(m.group(1))


LOTW_URL = "https://lotw.arrl.org/lotwuser/lotwreport.adi"


def lotw_fetch(user, password, start, end, timeout=180):
    """Fetch ADIF from LoTW's report service (query-qsos-qsls).

    LoTW only returns DXCC / COUNTRY / GRIDSQUARE for QSOs that are already
    confirmed (QSL_RCVD=Y), so unconfirmed QSOs are usable for the counter but
    cannot be placed on the map.  Unconfirmed ones are skipped by the caller.

    Credentials are the lotw.arrl.org website login (usually the callsign),
    NOT the TQSL certificate passphrase.
    """
    params = {
        "login": user,
        "password": password,
        "qso_query": "1",
        "qso_qsl": "no",         # QSO records
        "qso_qsldetail": "yes",  # + DXCC / COUNTRY / GRIDSQUARE on confirmed QSOs
        "qso_withown": "yes",    # + STATION_CALLSIGN
        "qso_startdate": start,
        "qso_enddate": end,
    }
    url = LOTW_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")

    if "<EOH>" not in raw.upper():
        m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
        if not m:
            m = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", raw, re.S | re.I)
        msg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else raw[:200]
        raise SystemExit(
            "LoTW returned no ADIF: %s\n"
            "Use the lotw.arrl.org login (usually your callsign) and password,\n"
            "not the TQSL certificate passphrase." % msg
        )
    return raw


def dedupe(recs):
    """Collapse QSOs present in more than one source (QRZ + LoTW + ADIF)."""
    best = {}
    for rec in recs:
        key = (
            (rec.get("CALL") or "").upper().strip(),
            (rec.get("QSO_DATE") or "").strip(),
            (rec.get("TIME_ON") or "").strip()[:4],
            (rec.get("BAND") or "").upper().strip(),
            (rec.get("MODE") or "").upper().strip(),
        )
        if not key[0] or not key[1]:
            continue
        cur = best.get(key)
        if cur is None:
            best[key] = rec
            continue
        cur_loc = bool(cur.get("GRIDSQUARE") or (cur.get("LAT") and cur.get("LON")))
        new_loc = bool(rec.get("GRIDSQUARE") or (rec.get("LAT") and rec.get("LON")))
        if not cur_loc and new_loc:
            best[key] = rec
    return list(best.values())


def demo_adif(path, n=340):
    random.seed(4)
    places = [
        ("Europe", 50, 10, 12), ("Europe", 45, 0, 8), ("Japan", 36, 138, 10),
        ("Japan", 34, 132, 6), ("United States", 39, -98, 14),
        ("United States", 33, -117, 6), ("Canada", 50, -100, 5),
        ("Brazil", -15, -50, 5), ("Argentina", -35, -62, 3), ("Chile", -33, -70, 2),
        ("South Africa", -29, 25, 3), ("Australia", -30, 140, 5), ("New Zealand", -41, 174, 2),
        ("China", 33, 110, 8), ("Taiwan", 24, 121, 4), ("South Korea", 36, 128, 3),
        ("Thailand", 15, 101, 2), ("Indonesia", -2, 118, 3), ("India", 22, 79, 3),
        ("Russia", 57, 60, 6), ("Ukraine", 49, 32, 3), ("Turkey", 39, 35, 3),
        ("Israel", 31, 35, 2), ("United Arab Emirates", 24, 54, 2),
        ("Mexico", 23, -102, 3), ("Colombia", 5, -74, 2), ("Peru", -10, -76, 2),
        ("Finland", 62, 26, 3), ("Norway", 61, 9, 3), ("Sweden", 60, 15, 3),
        ("Poland", 52, 19, 3), ("Italy", 42, 12, 4), ("Spain", 40, -4, 4),
        ("United Kingdom", 53, -2, 6), ("France", 47, 2, 5), ("Germany", 51, 10, 7),
        ("Netherlands", 52, 5, 3), ("Belgium", 50.5, 4.5, 2), ("Switzerland", 47, 8, 2),
        ("Austria", 47.5, 14, 2), ("Czechia", 50, 15, 2), ("Hungary", 47, 19, 2),
        ("Romania", 46, 25, 2), ("Bulgaria", 42.7, 25, 2), ("Greece", 39, 22, 2),
        ("Portugal", 39.5, -8, 2), ("Ireland", 53, -8, 2), ("Iceland", 65, -18, 1),
        ("Morocco", 32, -6, 2), ("Egypt", 27, 30, 2), ("Kenya", -1, 37, 2),
        ("Nigeria", 9, 8, 2), ("Algeria", 28, 3, 2), ("Tunisia", 34, 9, 1),
        ("Saudi Arabia", 24, 45, 2), ("Qatar", 25, 51, 1), ("Oman", 21, 57, 1),
        ("Pakistan", 30, 70, 2), ("Bangladesh", 24, 90, 1), ("Sri Lanka", 7, 81, 1),
        ("Malaysia", 4, 102, 2), ("Philippines", 12, 122, 2), ("Vietnam", 16, 107, 2),
        ("Kazakhstan", 48, 67, 2), ("Uzbekistan", 41, 65, 1), ("Georgia", 42, 44, 1),
        ("Armenia", 40, 45, 1), ("Azerbaijan", 40, 47, 1), ("Cyprus", 35, 33, 1),
        ("Cuba", 22, -80, 1), ("Dominican Republic", 19, -70, 1), ("Panama", 9, -80, 1),
        ("Costa Rica", 10, -84, 1), ("Ecuador", -2, -78, 1), ("Uruguay", -33, -56, 1),
        ("Paraguay", -23, -58, 1), ("Bolivia", -17, -65, 1), ("Venezuela", 8, -66, 1),
        ("Guatemala", 15, -90, 1), ("Puerto Rico", 18, -66, 1), ("Jamaica", 18, -77, 1),
        ("Trinidad and Tobago", 10.5, -61, 1), ("Aruba", 12.5, -70, 1),
        ("Hawaii", 20.8, -156, 2), ("Guam", 13.4, 144.8, 1), ("Fiji", -17.7, 178, 1),
        ("New Caledonia", -22, 166, 1), ("Reunion", -21, 55, 1), ("Mauritius", -20, 57, 1),
        ("Madagascar", -19, 47, 1), ("Mozambique", -19, 35, 1), ("Ghana", 7, -1, 1),
        ("Senegal", 14, -14, 1), ("Ivory Coast", 7, -5, 1), ("Cameroon", 5, 12, 1),
        ("Angola", -12, 18, 1), ("Namibia", -22, 17, 1), ("Botswana", -22, 24, 1),
        ("Zimbabwe", -19, 29, 1), ("Zambia", -15, 27, 1), ("Uganda", 1, 32, 1),
        ("Ethiopia", 9, 39, 1), ("Sudan", 15, 30, 1), ("Libya", 27, 17, 1),
        ("Iraq", 33, 44, 1), ("Jordan", 31, 36, 1), ("Lebanon", 34, 36, 1),
        ("Kuwait", 29, 48, 1), ("Bahrain", 26, 50.5, 1), ("Nepal", 27, 85, 1),
        ("Mongolia", 46, 103, 1), ("Myanmar", 21, 96, 1), ("Cambodia", 12, 105, 1),
        ("Singapore", 1.3, 103.8, 2), ("Hong Kong", 22.3, 114.2, 3), ("Macao", 22.2, 113.5, 1),
        ("Papua New Guinea", -6, 145, 1), ("Solomon Islands", -9.6, 160, 1),
        ("Samoa", -13.8, -172, 1), ("Tonga", -21, -175, 1), ("Vanuatu", -15.4, 167, 1),
        ("Maldives", 3.2, 73.2, 1), ("Seychelles", -4.7, 55.5, 1),
    ]
    weights = [p[3] for p in places]
    rows = []
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for i in range(n):
        name, lat, lon, _ = random.choices(places, weights=weights, k=1)[0]
        # more QSOs recently: age in days with a heavy tail
        age = int(min(720, (random.random() ** 1.8) * 720))
        d = now - dt.timedelta(days=age, hours=random.randint(0, 23), minutes=random.randint(0, 59))
        glat = lat + random.uniform(-2.2, 2.2)
        glon = lon + random.uniform(-2.2, 2.2)
        grid = to_maidenhead(glat, glon)
        band = random.choice(["20M", "20M", "20M", "40M", "15M", "17M", "30M", "10M", "80M", "12M"])
        mode = random.choice(["FT8", "FT8", "FT8", "FT4", "CW", "SSB", "FT8", "CW"])
        call = "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(2)) + str(random.randint(1, 9)) + \
               "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(3))
        rows.append(
            "<call:%d>%s<qso_date:8>%s<time_on:6>%s<band:%d>%s<mode:%d>%s<gridsquare:%d>%s<country:%d>%s<eor>"
            % (len(call), call, d.strftime("%Y%m%d"), d.strftime("%H%M%S"),
               len(band), band, len(mode), mode, len(grid), grid, len(name), name)
        )
    with open(path, "w", encoding="utf-8") as f:
        f.write("BA4RKS demo log\n<EOH>\n" + "\n".join(rows) + "\n")
    return path


def to_maidenhead(lat, lon):
    lon = max(-180.0, min(179.99, lon))
    lat = max(-90.0, min(89.99, lat))
    A = ord("A")
    lon += 180
    lat += 90
    f1 = int(lon / 20); f2 = int(lat / 10)
    lon -= f1 * 20; lat -= f2 * 10
    s1 = int(lon / 2); s2 = int(lat)
    lon -= s1 * 2; lat -= s2
    return "%s%s%d%d%s%s" % (chr(A + f1), chr(A + f2), s1, s2,
                             chr(A + int(lon * 12)), chr(A + int(lat * 24)))


# ---------------------------------------------------------------- render
def render_svg(geo, points, caption_left, caption_right, path):
    cols, rows = geo.cols, geo.rows
    w = PAD_X * 2 + cols * PITCH
    h = PAD_TOP + rows * PITCH + PAD_BOTTOM
    out = []
    out.append(
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
        'role="img" aria-label="QSO dot map">' % (round(w), round(h), round(w), round(h))
    )
    # land dots
    land = []
    for r, row in enumerate(geo.mask):
        for c, bit in enumerate(row):
            if bit == "1":
                x = PAD_X + (c + 0.5) * PITCH
                y = PAD_TOP + (r + 0.5) * PITCH
                land.append('<circle cx="%.1f" cy="%.1f" r="%.1f"/>' % (x, y, LAND_R))
    out.append('<g fill="%s" opacity="%.2f">%s</g>' % (LAND_FILL, LAND_OPACITY, "".join(land)))

    # QSO dots, oldest first
    lit = []
    for p in sorted(points, key=lambda p: p["age"]):
        x = PAD_X + (p["col"] + 0.5) * PITCH
        y = PAD_TOP + (p["row"] + 0.5) * PITCH
        if p["age"] <= 7:
            lit.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" opacity="0.14"/>'
                       % (x, y, p["size"] + 3.0, DOT_FILL))
        fill = DOT_FILL_NEW if p["age"] <= 30 else DOT_FILL
        lit.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" opacity="%.2f"/>'
                   % (x, y, p["size"], fill, p["op"]))
    out.append('<g>%s</g>' % "".join(lit))

    out.append('<text x="%.0f" y="%.0f" font-family="%s" font-size="12" fill="%s">%s</text>'
               % (PAD_X, h - 12, MONO, CAPTION_FILL, esc(caption_left)))
    out.append('<text x="%.0f" y="%.0f" font-family="%s" font-size="12" fill="%s" text-anchor="end">%s</text>'
               % (w - PAD_X, h - 12, MONO, CAPTION_FILL, esc(caption_right)))
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return w, h


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def svg_to_png(svg_path, png_path, w, h, scale=2):
    cands = [
        os.environ.get("CHROME"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    exe = next((c for c in cands if c and os.path.exists(c)), None)
    if not exe:
        print("! chrome/edge not found - PNG skipped, use the SVG", file=sys.stderr)
        return False
    url = "file:///" + os.path.abspath(svg_path).replace("\\", "/")
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-crash-reporter",
           "--hide-scrollbars", "--force-device-scale-factor=%d" % scale,
           "--default-background-color=00000000",
           "--window-size=%d,%d" % (round(w), round(h)),
           "--screenshot=" + os.path.abspath(png_path), url]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    return os.path.exists(png_path)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Render a QSO dot-matrix world map")
    ap.add_argument("--key", default=os.environ.get("QRZ_API_KEY"), help="QRZ Logbook API key")
    ap.add_argument("--adif", action="append", help="ADIF file; repeat to merge (e.g. --adif qrz.adi --adif lotw.adi)")
    ap.add_argument("--lotw-user", default=os.environ.get("LOTW_USER"), help="LoTW login (usually the callsign)")
    ap.add_argument("--lotw-pass", default=os.environ.get("LOTW_PASS"), help="LoTW website password")
    ap.add_argument("--demo", action="store_true", help="use synthetic demo data")
    ap.add_argument("--months", type=int, default=24, help="recency window in months (default 24)")
    ap.add_argument("--out", default=".", help="output directory")
    ap.add_argument("--name", default="qso-map", help="output file base name")
    ap.add_argument("--png", action="store_true", help="also render a 2x PNG via Chrome")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    geo = Geo()

    end = dt.date.today().isoformat()
    start = (dt.date.today() - dt.timedelta(days=args.months * 31)).isoformat()

    sources = []  # [(label, adif text)]
    if args.demo:
        adif_path = os.path.join(args.out, "demo.adi")
        demo_adif(adif_path)
        sources.append(("demo", open(adif_path, encoding="utf-8").read()))
    for path in args.adif or []:
        sources.append((os.path.basename(path),
                        open(path, encoding="utf-8", errors="replace").read()))
    if args.key:
        sources.append(("QRZ", qrz_fetch(args.key, start=start, end=end)))
    if args.lotw_user or args.lotw_pass:
        if not (args.lotw_user and args.lotw_pass):
            ap.error("--lotw-user and --lotw-pass must be used together")
        sources.append(("LoTW", lotw_fetch(args.lotw_user, args.lotw_pass, start=start, end=end)))
    if not sources:
        ap.error("give at least one of --demo / --adif / --key / --lotw-user")

    recs, counts = [], []
    for label, text in sources:
        rs = parse_adif(text)
        counts.append("%s %d" % (label, len(rs)))
        recs.extend(rs)
    if not recs:
        raise SystemExit("no QSO records found")

    source = " + ".join(counts)
    unique = dedupe(recs)
    if len(unique) != len(recs):
        source += " -> %d unique" % len(unique)
    recs = unique

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    window = args.months * 30.44
    pts, skipped, outside = {}, 0, 0
    dates = []
    for rec in recs:
        raw = (rec.get("QSO_DATE") or "").strip()
        try:
            d = dt.datetime.strptime(raw, "%Y%m%d")
        except ValueError:
            skipped += 1
            continue
        age = max(0.0, (now - d).total_seconds() / 86400.0)
        if age > window:
            continue
        lat = lon = None
        if rec.get("GRIDSQUARE"):
            ll = maidenhead(rec["GRIDSQUARE"])
            if ll:
                lat, lon = ll
        if lat is None and rec.get("LAT") and rec.get("LON"):
            try:
                lat, lon = float(rec["LAT"]), float(rec["LON"])
            except ValueError:
                pass
        if lat is None:
            ll = geo.country_latlon(rec.get("COUNTRY"))
            if ll:
                lat, lon = ll
        if lat is None:
            skipped += 1
            continue
        cell = geo.cell(lat, lon)
        if not cell:
            outside += 1
            continue
        c, r = cell
        t = min(1.0, age / window)
        size = R_OLD + (R_NEW - R_OLD) * (1.0 - t) ** 0.8
        op = 1.0 - 0.35 * t
        prev = pts.get(cell)
        if prev is None or age < prev["age"]:
            pts[cell] = {"col": c, "row": r, "age": age, "size": size, "op": op}
        dates.append(d)

    points = list(pts.values())

    if dates:
        span = "%s — %s" % (min(dates).strftime("%Y.%m"), max(dates).strftime("%Y.%m"))
    else:
        span = "no QSOs in window"
    left = "%d QSOs · %s · %s" % (len(dates), span, "dot size = recency")
    right = "updated %s" % now.strftime("%Y-%m-%d")

    svg_path = os.path.join(args.out, args.name + ".svg")
    w, h = render_svg(geo, points, left, right, svg_path)
    print("source: %s | records: %d | mapped: %d | cells lit: %d | skipped: %d | outside grid: %d"
          % (source, len(recs), len(dates), len(points), skipped, outside))
    print("wrote %s (%.0fx%.0f)" % (svg_path, w, h))

    if args.png:
        png_path = os.path.join(args.out, args.name + ".png")
        ok = svg_to_png(svg_path, png_path, w, h, scale=2)
        print("wrote %s" % png_path if ok else "png failed")


if __name__ == "__main__":
    main()
