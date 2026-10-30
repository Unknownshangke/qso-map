#!/usr/bin/env python3
"""Build static geo assets from world-atlas TopoJSON.

Outputs (into ./geo):
  land_mask.json          {"cols":120,"rows":56,"top":84,"bottom":-56,"left":-180,"right":180,
                           "mask":["0110...", ...]}   # one string per row, 1 = land
  country_centroids.json  {"Country name": [lat, lon], ...}

Run once; afterwards generate.py works offline.
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GEO = os.path.join(HERE, "geo")

COLS, ROWS = 120, 56
TOP_LAT, BOT_LAT = 84.0, -56.0          # clip Antarctica
LON_STEP = 360.0 / COLS
LAT_STEP = (TOP_LAT - BOT_LAT) / ROWS


def decode_arcs(topo):
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        pts, x, y = [], 0, 0
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    return arcs


def ring_coords(ring, arcs):
    out = []
    for i in ring:
        pts = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
        if out:
            pts = pts[1:]
        out.extend(pts)
    return out


def polygons_of(geom, arcs):
    """Return list of polygons, each a list of rings (lon, lat)."""
    t = geom["type"]
    if t == "Polygon":
        return [[ring_coords(r, arcs) for r in geom["arcs"]]]
    if t == "MultiPolygon":
        return [[ring_coords(r, arcs) for r in poly] for poly in geom["arcs"]]
    return []


def bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def polygon_contains(x, y, poly, boxes):
    inside = False
    for ring, bx in zip(poly, boxes):
        if x < bx[0] or x > bx[2] or y < bx[1] or y > bx[3]:
            continue
        if point_in_ring(x, y, ring):
            inside = not inside
    return inside


def ring_area_centroid(ring):
    a = cx = cy = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-12:
        return 0.0, 0.0, 0.0
    a *= 0.5
    return abs(a), cx / (6 * a), cy / (6 * a)


def build_land():
    topo = json.load(open(os.path.join(GEO, "land-110m.json"), encoding="utf-8"))
    arcs = decode_arcs(topo)
    geom = topo["objects"]["land"]["geometries"][0]
    polys = polygons_of(geom, arcs)
    prepped = []
    for poly in polys:
        boxes = [bbox(r) for r in poly]
        x0 = min(b[0] for b in boxes); x1 = max(b[2] for b in boxes)
        y0 = min(b[1] for b in boxes); y1 = max(b[3] for b in boxes)
        prepped.append((poly, boxes, (x0, y0, x1, y1)))

    rows = []
    for r in range(ROWS):
        lat = TOP_LAT - (r + 0.5) * LAT_STEP
        bits = []
        for c in range(COLS):
            lon = -180.0 + (c + 0.5) * LON_STEP
            land = False
            for poly, boxes, bb in prepped:
                if lon < bb[0] or lon > bb[2] or lat < bb[1] or lat > bb[3]:
                    continue
                if polygon_contains(lon, lat, poly, boxes):
                    land = True
                    break
            bits.append("1" if land else "0")
        rows.append("".join(bits))
        print("row %02d/%d  %s" % (r + 1, ROWS, "".join("#" if b == "1" else "." for b in bits)), flush=True)

    data = {"cols": COLS, "rows": ROWS, "top": TOP_LAT, "bottom": BOT_LAT,
            "left": -180.0, "right": 180.0, "mask": rows}
    json.dump(data, open(os.path.join(GEO, "land_mask.json"), "w", encoding="utf-8"))
    print("land cells:", sum(row.count("1") for row in rows))


def build_countries():
    topo = json.load(open(os.path.join(GEO, "countries-110m.json"), encoding="utf-8"))
    arcs = decode_arcs(topo)
    out = {}
    for geom in topo["objects"]["countries"]["geometries"]:
        name = (geom.get("properties") or {}).get("name")
        if not name:
            continue
        polys = polygons_of(geom, arcs)
        best = (0.0, None, None)
        for poly in polys:
            outer = poly[0]
            area, cx, cy = ring_area_centroid(outer)
            if area > best[0]:
                best = (area, cx, cy)
        if best[1] is not None:
            out[name] = [round(best[2], 2), round(best[1], 2)]

    # small entities that the 110m dataset does not carry (or carries merged)
    extra = {
        "Hong Kong": [22.32, 114.17], "Macao": [22.19, 113.54],
        "Singapore": [1.35, 103.82], "Bahrain": [26.07, 50.55],
        "Andorra": [42.55, 1.57], "Liechtenstein": [47.15, 9.55],
        "Monaco": [43.74, 7.42], "San Marino": [43.94, 12.46],
        "Vatican City": [41.90, 12.45], "Malta": [35.90, 14.44],
        "Luxembourg": [49.82, 6.13], "Cyprus": [35.13, 33.43],
        "Kosovo": [42.60, 20.90], "Palestine": [31.95, 35.23],
        "Djibouti": [11.83, 42.59], "Equatorial Guinea": [1.65, 10.27],
        "Gambia": [13.44, -15.31], "Guinea-Bissau": [12.03, -14.97],
        "Comoros": [-11.65, 43.33], "Maldives": [3.20, 73.22],
        "Seychelles": [-4.68, 55.49], "Mauritius": [-20.35, 57.55],
        "Cape Verde": [16.00, -24.01], "Sao Tome and Principe": [0.19, 6.61],
        "Bahamas": [25.03, -77.40], "Barbados": [13.19, -59.54],
        "Antigua and Barbuda": [17.06, -61.80], "Dominica": [15.41, -61.37],
        "Grenada": [12.12, -61.68], "Saint Lucia": [13.91, -60.98],
        "Saint Vincent and the Grenadines": [13.25, -61.20],
        "Saint Kitts and Nevis": [17.36, -62.78],
        "Trinidad and Tobago": [10.69, -61.22],
        "Fiji": [-17.71, 178.07], "Samoa": [-13.76, -172.10],
        "Tonga": [-21.18, -175.20], "Kiribati": [1.87, -157.36],
        "Nauru": [-0.52, 166.93], "Tuvalu": [-8.52, 179.20],
        "Palau": [7.51, 134.58], "Marshall Islands": [7.13, 171.18],
        "Micronesia": [7.43, 150.55], "Vanuatu": [-15.38, 166.96],
        "Solomon Islands": [-9.65, 160.16],
        "Brunei": [4.54, 114.73], "Timor-Leste": [-8.87, 125.73],
    }
    for k, v in extra.items():
        out.setdefault(k, v)
    json.dump(out, open(os.path.join(GEO, "country_centroids.json"), "w", encoding="utf-8"),
              indent=0, sort_keys=True)
    print("countries:", len(out))


if __name__ == "__main__":
    os.makedirs(GEO, exist_ok=True)
    if not os.path.exists(os.path.join(GEO, "land-110m.json")):
        sys.exit("missing geo/land-110m.json - download world-atlas first")
    build_land()
    build_countries()
    print("done")
