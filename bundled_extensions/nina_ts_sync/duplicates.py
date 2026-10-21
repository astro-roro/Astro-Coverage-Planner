"""Find new TS plans that cover the same sky as a plan ACP already has.

A plan's footprint is the rectangle ACP draws for it: the field of view from
its telescope and camera (app._fov_arcmin), grown to the whole mosaic with
the panel stride ACP uses (fov * (1 - overlap), as in app._mosaic_panel_centers
and shooting_publish._mosaic_extent_arcmin), turned by the plan's rotation
east of north, and centred on the plan's centre.

ACP places panels with a flat offset (RA offset divided by cos Dec). This
module puts each rectangle on a gnomonic tangent plane at its own centre
instead, which is the same thing for fields a few degrees across and stays
right across RA 0/360 and near a pole. A gnomonic projection maps great
circles to straight lines, so a rectangle drawn on one tangent plane is still
a four-sided convex polygon on another, and the overlap is an exact polygon
clip on the plane both are projected onto.

Names play no part here. That is the point: a profile cloned from another,
or one profile used on two rigs, gives TS projects whose names ACP never saw.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Flag a pair when the overlap covers at least this share of the smaller
# footprint, or the centres are within half the smaller field of view.
FLAG_OVERLAP = 0.30
# Above this share the page defaults to skipping the TS plan.
STRONG_OVERLAP = 0.70


# ---------------------------------------------------------------------------
# Footprints
# ---------------------------------------------------------------------------


def fov_arcmin(telescope: dict | None, camera: dict | None) -> tuple[float, float]:
    """app._fov_arcmin, unrounded. (0, 0) when the gear cannot give one."""
    if not telescope or not camera:
        return (0.0, 0.0)
    try:
        fl = float(telescope["focal_length_mm"])
        px_um = float(camera["pixel_size_um"])
        w_px, h_px = camera["sensor_px"]
        w_px, h_px = float(w_px), float(h_px)
    except (KeyError, ValueError, TypeError):
        return (0.0, 0.0)
    if fl <= 0 or px_um <= 0 or w_px <= 0 or h_px <= 0:
        return (0.0, 0.0)
    aspp = 206.265 * px_um / fl
    return (w_px * aspp / 60.0, h_px * aspp / 60.0)


def mosaic_extent_arcmin(fov_w: float, fov_h: float, mosaic: dict | None) -> tuple[float, float]:
    """shooting_publish._mosaic_extent_arcmin: the whole mosaic's size."""
    m = mosaic or {}
    try:
        rows = max(1, int(m.get("rows") or 1))
        cols = max(1, int(m.get("cols") or 1))
        ov = max(0.0, min(0.99, float(m.get("overlap_pct") or 0) / 100.0))
    except (TypeError, ValueError):
        rows, cols, ov = 1, 1, 0.0
    return (fov_w * (1 + (cols - 1) * (1 - ov)), fov_h * (1 + (rows - 1) * (1 - ov)))


@dataclass
class Footprint:
    ra: float          # centre, degrees
    dec: float         # centre, degrees
    w: float           # whole footprint, arcmin (0 when the gear gives none)
    h: float
    rot: float         # degrees east of north, as ACP stores it

    @property
    def known(self) -> bool:
        return self.w > 0 and self.h > 0

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def radius_deg(self) -> float:
        return math.hypot(self.w, self.h) / 2.0 / 60.0

    def corners(self) -> list[tuple[float, float]]:
        """The four corners as (ra, dec) in degrees, anticlockwise on the
        sky as seen from inside (north, then east, is anticlockwise)."""
        r = math.radians(self.rot or 0.0)
        cos_r, sin_r = math.cos(r), math.sin(r)
        out = []
        for cx, cy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            x, y = cx * self.w / 2.0, cy * self.h / 2.0
            # app._mosaic_panel_centers: camera frame into (east, north).
            de = x * cos_r + y * sin_r
            dn = -x * sin_r + y * cos_r
            out.append(deproject(de / 60.0, dn / 60.0, self.ra, self.dec))
        return out


def footprint_of(plan: dict, gear: dict, *, telescope_id=None, camera_id=None) -> Footprint | None:
    """The plan's footprint, or None when it has no usable centre. The gear
    ids override the plan's own, for a new single target whose gear is the
    one the page picks."""
    tg = plan.get("target") or {}
    try:
        ra = float(tg.get("center_ra_deg"))
        dec = float(tg.get("center_dec_deg"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(ra) and math.isfinite(dec)) or abs(dec) > 90:
        return None
    try:
        rot = float(tg.get("rotation_deg") or 0.0)
    except (TypeError, ValueError):
        rot = 0.0
    tel = _by_id(gear.get("telescopes"), telescope_id or plan.get("telescope_id"))
    cam = _by_id(gear.get("cameras"), camera_id or plan.get("camera_id"))
    fw, fh = fov_arcmin(tel, cam)
    w, h = mosaic_extent_arcmin(fw, fh, tg.get("mosaic"))
    return Footprint(ra=ra % 360.0, dec=dec, w=w, h=h, rot=rot)


def _by_id(items, wanted) -> dict | None:
    for it in items or []:
        if isinstance(it, dict) and wanted and it.get("id") == wanted:
            return it
    return None


# ---------------------------------------------------------------------------
# Sphere and tangent plane
# ---------------------------------------------------------------------------


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    a = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


def project(ra: float, dec: float, ra0: float, dec0: float) -> tuple[float, float] | None:
    """Gnomonic projection about (ra0, dec0). Returns (east, north) in
    degrees on the tangent plane, or None for a point 80 degrees or more away."""
    d, d0 = math.radians(dec), math.radians(dec0)
    dra = math.radians(ra - ra0)
    cosc = math.sin(d0) * math.sin(d) + math.cos(d0) * math.cos(d) * math.cos(dra)
    if cosc < 0.17:
        return None
    xi = math.cos(d) * math.sin(dra) / cosc
    eta = (math.cos(d0) * math.sin(d) - math.sin(d0) * math.cos(d) * math.cos(dra)) / cosc
    return (math.degrees(xi), math.degrees(eta))


def deproject(xi_deg: float, eta_deg: float, ra0: float, dec0: float) -> tuple[float, float]:
    """The inverse of `project`: (east, north) degrees back to (ra, dec)."""
    xi, eta = math.radians(xi_deg), math.radians(eta_deg)
    d0 = math.radians(dec0)
    rho = math.hypot(xi, eta)
    if rho == 0:
        return (ra0 % 360.0, dec0)
    c = math.atan(rho)
    sin_c, cos_c = math.sin(c), math.cos(c)
    dec = math.asin(max(-1.0, min(1.0, cos_c * math.sin(d0) + eta * sin_c * math.cos(d0) / rho)))
    ra = ra0 + math.degrees(math.atan2(xi * sin_c, rho * math.cos(d0) * cos_c - eta * math.sin(d0) * sin_c))
    return (ra % 360.0, math.degrees(dec))


# ---------------------------------------------------------------------------
# Polygons on the plane
# ---------------------------------------------------------------------------


def _area(poly: list[tuple[float, float]]) -> float:
    s = 0.0
    for i, (x1, y1) in enumerate(poly):
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _clip(subject: list[tuple[float, float]], clip: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sutherland-Hodgman: *subject* cut to the convex, anticlockwise *clip*."""
    out = subject
    for i, (ax, ay) in enumerate(clip):
        bx, by = clip[(i + 1) % len(clip)]
        inp, out = out, []
        if not inp:
            break

        def side(p):
            return (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)

        for j, p in enumerate(inp):
            q = inp[(j + 1) % len(inp)]
            sp, sq = side(p), side(q)
            if sp >= 0:
                out.append(p)
            if (sp >= 0) != (sq >= 0):
                t = sp / (sp - sq)
                out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def _on_plane(fp: Footprint, ra0: float, dec0: float) -> list[tuple[float, float]] | None:
    pts = []
    for ra, dec in fp.corners():
        p = project(ra, dec, ra0, dec0)
        if p is None:
            return None
        pts.append(p)
    if _area(pts) < 0:
        pts.reverse()
    return pts


def overlap_fraction(a: Footprint, b: Footprint) -> float | None:
    """How much of the smaller footprint the other covers, 0 to 1. None when
    either field of view is unknown."""
    if not (a.known and b.known):
        return None
    if separation_deg(a.ra, a.dec, b.ra, b.dec) >= a.radius_deg + b.radius_deg:
        return 0.0
    # Project onto the plane at the midpoint so neither side is favoured.
    mra, mdec = _midpoint(a, b)
    pa, pb = _on_plane(a, mra, mdec), _on_plane(b, mra, mdec)
    if pa is None or pb is None:
        return None
    inter = _clip(pa, pb)
    if len(inter) < 3:
        return 0.0
    smaller = min(abs(_area(pa)), abs(_area(pb)))
    if smaller <= 0:
        return None
    return max(0.0, min(1.0, abs(_area(inter)) / smaller))


def _midpoint(a: Footprint, b: Footprint) -> tuple[float, float]:
    va, vb = _unit(a.ra, a.dec), _unit(b.ra, b.dec)
    x, y, z = va[0] + vb[0], va[1] + vb[1], va[2] + vb[2]
    n = math.sqrt(x * x + y * y + z * z)
    if n < 1e-12:
        return (a.ra, a.dec)
    return (math.degrees(math.atan2(y, x)) % 360.0, math.degrees(math.asin(max(-1.0, min(1.0, z / n)))))


def _unit(ra: float, dec: float) -> tuple[float, float, float]:
    r, d = math.radians(ra), math.radians(dec)
    return (math.cos(d) * math.cos(r), math.cos(d) * math.sin(r), math.sin(d))


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


@dataclass
class Comparison:
    overlap: float | None   # share of the smaller footprint, 0 to 1
    separation_arcmin: float
    flagged: bool
    strong: bool


def compare(a: Footprint, b: Footprint) -> Comparison:
    sep = separation_deg(a.ra, a.dec, b.ra, b.dec) * 60.0
    frac = overlap_fraction(a, b)
    # Half the smaller field of view: the shorter side of the smaller
    # footprint, of those whose size is known.
    known = [f for f in (a, b) if f.known]
    near = False
    if known:
        smaller = min(known, key=lambda f: f.area)
        near = sep <= min(smaller.w, smaller.h) / 2.0
    flagged = near or (frac is not None and frac >= FLAG_OVERLAP)
    return Comparison(overlap=frac, separation_arcmin=sep, flagged=flagged,
                      strong=flagged and frac is not None and frac > STRONG_OVERLAP)


def best_match(fp: Footprint, candidates: list[tuple[object, Footprint]]) -> tuple[object, Comparison] | None:
    """The flagged candidate *fp* overlaps most, closest centre breaking a
    tie. A candidate further away than both footprints and the centre rule
    could reach is dismissed on its separation alone, which keeps a few
    hundred against a few hundred quick."""
    best = None
    ux, uy, uz = _unit(fp.ra, fp.dec)
    for item, other in candidates:
        vx, vy, vz = _unit(other.ra, other.dec)
        cos_sep = max(-1.0, min(1.0, ux * vx + uy * vy + uz * vz))
        sep_deg = math.degrees(math.acos(cos_sep))
        reach = fp.radius_deg + other.radius_deg
        if sep_deg > reach and sep_deg > 0.01:
            continue
        c = compare(fp, other)
        if not c.flagged:
            continue
        rank = (c.overlap if c.overlap is not None else -1.0, -c.separation_arcmin)
        if best is None or rank > best[0]:
            best = (rank, item, c)
    if best is None:
        return None
    return best[1], best[2]


# ---------------------------------------------------------------------------
# The sky view the review page draws
# ---------------------------------------------------------------------------

_DEC_STEPS_ARCMIN = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800]
# RA steps in seconds of time: 1s up to 6h.
_RA_STEPS_S = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600]


def _nice(steps: list[float], span: float, lines: int = 4) -> float:
    for s in steps:
        if span / s <= lines:
            return s
    return steps[-1]


def _fmt_ra(deg: float, step_s: float) -> str:
    total = round((deg % 360.0) / 15.0 * 3600.0) % 86400
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}h{m:02d}m{s:02d}s" if step_s < 60 else f"{h:02d}h{m:02d}m"


def _fmt_dec(deg: float, step_arcmin: float) -> str:
    sign = "-" if deg < -1e-9 else "+"
    total = round(abs(deg) * 60.0)
    d, m = divmod(total, 60)
    return f"{sign}{d}°" if step_arcmin >= 60 and m == 0 else f"{sign}{d}°{m:02d}′"


def sky_view(a: Footprint, b: Footprint) -> dict | None:  # a: the TS plan, b: the ACP plan
    """Both footprints and an RA/Dec grid on one tangent plane, in arcmin,
    x growing west (east is on the left, as the sky looks from below) and y
    growing north. None when neither footprint has a size to draw."""
    known = [f for f in (a, b) if f.known]
    if not known:
        return None
    ra0, dec0 = _midpoint(a, b)

    def poly(fp: Footprint) -> list[list[float]] | None:
        if not fp.known:
            return None
        pts = _on_plane(fp, ra0, dec0)
        if pts is None:
            return None
        return [[round(-x * 60.0, 3), round(y * 60.0, 3)] for x, y in pts]

    def centre(fp: Footprint) -> list[float] | None:
        p = project(fp.ra, fp.dec, ra0, dec0)
        return None if p is None else [round(-p[0] * 60.0, 3), round(p[1] * 60.0, 3)]

    pa, pb = poly(a), poly(b)
    xs, ys = [], []
    for pts in (pa, pb):
        for x, y in pts or []:
            xs.append(x)
            ys.append(y)
    for c in (centre(a), centre(b)):
        if c:
            xs.append(c[0])
            ys.append(c[1])
    if not xs:
        return None
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    pad = span * 0.12
    box = [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]
    # Square the box so the grid is not stretched on a narrow screen.
    w, h = box[2] - box[0], box[3] - box[1]
    if w < h:
        box[0] -= (h - w) / 2
        box[2] += (h - w) / 2
    else:
        box[1] -= (w - h) / 2
        box[3] += (w - h) / 2

    # The RA and Dec the box covers, from a lattice of points inside it.
    ras, decs = [], []
    for i in range(9):
        for j in range(9):
            x = box[0] + (box[2] - box[0]) * i / 8
            y = box[1] + (box[3] - box[1]) * j / 8
            ra, dec = deproject(-x / 60.0, y / 60.0, ra0, dec0)
            ras.append(((ra - ra0 + 180.0) % 360.0) - 180.0)
            decs.append(dec)
    pole = None
    for sign in (1, -1):
        p = project(0.0, 90.0 * sign, ra0, dec0)
        if p is not None:
            px, py = -p[0] * 60.0, p[1] * 60.0
            if box[0] <= px <= box[2] and box[1] <= py <= box[3]:
                pole = sign
    dec_lo, dec_hi = min(decs), max(decs)
    if pole == 1:
        dec_hi = 90.0
    if pole == -1:
        dec_lo = -90.0
    ra_lo, ra_hi = (-180.0, 180.0) if pole else (min(ras), max(ras))

    dec_step = _nice(_DEC_STEPS_ARCMIN, (dec_hi - dec_lo) * 60.0) / 60.0
    ra_step_s = _nice(_RA_STEPS_S, (ra_hi - ra_lo) / 15.0 * 3600.0)
    ra_step = ra_step_s * 15.0 / 3600.0

    def inside(x, y):
        return box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def line(points: list[tuple[float, float]]) -> list[list[float]]:
        out = []
        for ra, dec in points:
            p = project(ra, dec, ra0, dec0)
            if p is not None:
                out.append([round(-p[0] * 60.0, 3), round(p[1] * 60.0, 3)])
        return out

    grid = []
    n = 32
    d = math.floor(dec_lo / dec_step) * dec_step
    while d <= dec_hi + 1e-9:
        if abs(d) < 90.0 - 1e-9:
            pts = line([(ra0 + ra_lo + (ra_hi - ra_lo) * k / n, d) for k in range(n + 1)])
            ins = [p for p in pts if inside(*p)]
            if ins:
                anchor = min(ins, key=lambda p: p[0])
                grid.append({"kind": "dec", "label": _fmt_dec(d, dec_step * 60.0), "points": pts, "anchor": anchor})
        d += dec_step
    r = math.floor((ra0 + ra_lo) / ra_step) * ra_step
    count = 0
    seen: set[float] = set()
    while r <= ra0 + ra_hi + 1e-9 and count < 48:
        count += 1
        key = round(r % 360.0, 6)
        if key in seen:
            r += ra_step
            continue
        seen.add(key)
        lo, hi = max(-89.99, dec_lo), min(89.99, dec_hi)
        pts = line([(r, lo + (hi - lo) * k / n) for k in range(n + 1)])
        ins = [p for p in pts if inside(*p)]
        if ins:
            anchor = min(ins, key=lambda p: p[1])
            grid.append({"kind": "ra", "label": _fmt_ra(r, ra_step_s), "points": pts, "anchor": anchor})
        r += ra_step

    return {
        "box": [round(v, 3) for v in box],
        "acp": {"polygon": pb, "centre": centre(b)},
        "ts": {"polygon": pa, "centre": centre(a)},
        "grid": grid,
    }
