# -*- coding: utf-8 -*-
"""시군구 GeoJSON → 시도별 인라인 SVG path (전국 지도와 동일 방식).
출력: mvp/geo/sigungu_svg.json = { 시도명: {width,height,viewBox,paths:[{name,code,d}],outline} }
- 시도별로 자기 bbox에 맞춰 정규화(WIDTH=1000) → 줌인 효과.
- 섬 당기기(classify_shift) 미적용: 시도 단위라 실제 지리 그대로.
- geo_to_svg 의 rdp/merc 재사용.
"""
import json
import os
import re
from collections import defaultdict

from geo_to_svg import rdp, merc, rings_of


def _scale_path_d(d, s, tx, ty):
    """path d-string의 M/L 좌표를 s배 스케일 + (tx,ty) 평행이동."""
    def repl(m):
        return f"{m.group(1)}{round(float(m.group(2))*s+tx,1)} {round(float(m.group(3))*s+ty,1)}"
    return re.sub(r"([ML])(-?\d+\.?\d*) (-?\d+\.?\d*)", repl, d)

BASE = os.path.dirname(os.path.abspath(__file__))

# 2013 행정구역(raw GeoJSON) → 현재 명칭 보정 (code 기준)
_RENAME_BY_CODE = {
    "23030": "미추홀구",   # 인천 남구 → 미추홀구 (2018.7 개명)
}
SIGUNGU = os.path.join(BASE, "geo", "sigungu_raw.json")
PROV = os.path.join(BASE, "geo", "skorea_provinces.json")
RIVERS_POLY = os.path.join(BASE, "geo", "rivers_poly_kr.geojson")
LAKES = os.path.join(BASE, "geo", "lakes_kr.geojson")
DST = os.path.join(BASE, "geo", "sigungu_svg.json")

WIDTH = 1000.0
PAD = 6.0
PREC = 1
EPS = 1.0
MIN_RING_PX = 1.2   # 렌더 최소크기(작은 섬도 표시 — 인천 옹진 군도 등 보존). 3.0→1.2


AREA_MIN_UNNAMED = 0.0006   # 무명 폴리곤은 이 면적(deg^2) 이상만(소하천 제거)


def _keep_river(name, area):
    """굵직한 주요 강만: 이름에 '강' 또는 '호/지'(천 제외), 또는 무명 대형."""
    if name:
        if name.endswith("천"):
            return False
        return ("강" in name) or name.endswith("호") or name.endswith("지")
    return area >= AREA_MIN_UNNAMED


def _load_water():
    """한강만 로드(서울 지도 전용). 밤섬·노들섬 등 hole 보존. 그 외 강·호수 미표시."""
    from shapely.geometry import shape
    geoms = []
    if os.path.exists(RIVERS_POLY):
        for f in json.load(open(RIVERS_POLY, encoding="utf-8")).get("features", []):
            nm = (f.get("properties") or {}).get("name") or ""
            if nm != "한강":
                continue
            try:
                g = shape(f["geometry"])
                if g.is_valid and not g.is_empty:
                    geoms.append(g)
            except Exception:
                pass
    return geoms


def _project_water(water, lon0, lon1, lat0, lat1, tx, ty):
    """수면 폴리곤을 시도 lon/lat bbox로 클리핑 → px path d(hole=evenodd subpath)."""
    from shapely.geometry import box
    mlon = (lon1 - lon0) * 0.02
    mlat = (lat1 - lat0) * 0.02
    clip = box(lon0 - mlon, lat0 - mlat, lon1 + mlon, lat1 + mlat)
    out = []
    for g in water:
        try:
            gg = g.intersection(clip)
        except Exception:
            continue
        if gg.is_empty:
            continue
        polys = [gg] if gg.geom_type == "Polygon" else (
            list(gg.geoms) if gg.geom_type in ("MultiPolygon", "GeometryCollection") else [])
        for poly in polys:
            if getattr(poly, "geom_type", "") != "Polygon":
                continue
            d = ""
            for ring in [poly.exterior] + list(poly.interiors):
                px = [(tx(merc(x, y)[0]), ty(merc(x, y)[1])) for x, y in ring.coords]
                simp = rdp(px, EPS)
                if len(simp) >= 3:
                    d += "M" + "L".join(f"{a} {b}" for a, b in simp) + "Z"
            if d:
                out.append(d)
    return out


# ── 섬 당기기(compaction) 2단계 ──
# A. feature 내부 ring 압축: 각 자치구의 본체(최대 ring) 쪽으로 자기 부속섬을 당김
#    (제주 추자·충남 태안섬·전남 신안·경남 통영/거제·경기 안산/화성 등 안전 처리)
# B. 섬 군(郡) 이동: 옹진·강화·울릉처럼 통째로 떨어진 섬 자치구를 축소+본토 옆 이동.
# 효과: bbox 축소 → 본토 확대(소형 자치구 클릭성↑) + 여백 감소.
_INTRA_GAP = {   # 시도: 부속섬 잔여거리 비율(↓=더 당김). 미지정=압축 안함
    "경상북도": 0.25, "제주특별자치도": 0.30, "충청남도": 0.28,
    "전라북도": 0.35, "경상남도": 0.55, "경기도": 0.32,
    # 인천=예시처럼 자연위치+극서단 제거만(이동·압축 없음). 전남=부서짐 제외. 부산=explode 취소.
}
# B. 이동 대상 섬 자치구: {시도: {자치구: (scale, 방향)}} 방향=W/NW/N/NE/E
#    각 자치구를 자기 중심 기준 균일 축소(섬 간 겹침 없음·크기 일관) 후 본토 옆 배치.
_MOVE_ISLAND = {}   # 인천=맨 처음 자연 지도로 복구(섬 이동·축소 없음).
# B'. 절대좌표 이동: {시도:{자치구:(scale, 목표lon, 목표lat)}} — 울릉/독도를 울진 높이로
_MOVE_ABS = {
    "경상북도": {"울릉군": (0.55, 129.92, 37.02)},   # 울진(상단 lat37.15) 동쪽·동일 높이
}
# C. 소형 자치구 확대(자기 중심 scale↑) — 클릭성. 인접 약간 겹침 허용.
_LOCAL_SCALE = {
    "강원도":     {"속초시": 1.7, "동해시": 1.7},
}
# D. 밀집 원도심 분리(explode) — 부산은 사용자 요청으로 취소(이전 버전 복귀). 필요 시 재활성.
_EXPLODE = {}
# E. 군단위 섬+바다 전부 hit: convex hull로 클릭영역 확장
_HULL_HIT = {
    "전라남도":  {"신안군", "진도군", "완도군"},
    "경상남도":  {"통영시", "거제시", "남해군"},
}   # 인천=맨 처음 자연 지도로 복구(hull 없음).
# F. 본토 당기기: 섬 자치구 ring을 본토(섬 외 자치구 bbox) 쪽으로 lon 압축(섬 형태 유지·내륙 클릭성↑)
#    keep=잔여거리비율(↓=최대한 당김), west_thresh=이 lon 서쪽 ring만(원거리 섬만 선택적)
_PULL_MAINLAND = {
    "전라남도":   {"islands": {"신안군"}, "keep": 0.42, "west_thresh": 125.95},  # 흑산·홍도·가거 등
}
_MOVE_GROUP = {}   # 강화 분리(자연유지) → 그룹 미사용. 옹진만 _MOVE_ISLAND.
# H. 극서단 섬 제거(그 자리는 바다 배경) — 예시 '흰 박스=바다'. {시도:{자치구: lon_미만}}
#    centroid lon이 기준 미만인 폴리곤 제거. 옹진 백령·대청·소청(lon<125.0)만(연평 125.65 유지·당김).
_DROP_FAR = {}   # 인천=섬 제거 없음(아래 _SCALE_TOWARD로 거리축소).
# I. 기준점 향해 균일 축소(거리·크기 ×ratio) — 추자처럼 먼 섬을 당김. 자치구별 ref.
#    {시도:{자치구:{ref, ratio, lon_lt(이 lon 서쪽 ring만)|None}}}.
_SCALE_TOWARD = {
    "인천광역시": {
        "옹진군": {"ref": (126.15, 37.46), "ratio": 0.42, "lon_lt": None},      # 본토 서측으로
        # 강화 서도면(말도·우도·볼음도·아차도·주문도)은 자연 순서 보존 위해 이동 안함.
    },
}
# J. 부분 균일축소: 자치구의 lon>=기준 ring만 ratio배(나머지 유지).
#    인천 강화 = 큰 섬(본섬·석모·교동, lon>=126.25)만 0.7x. 서도면 5섬(<126.25)은 자연 유지(순서 보존·우도 유지).
_PARTIAL_SCALE = {
    "인천광역시": {"강화군": {"ratio": 0.7, "lon_ge": 126.25}},
}
# G. 소형 자치구 시도 = 지도(자치구)만 축소, 도넛·라벨은 정상 폼. {시도: 본체 목표높이}
#    viewBox 높이 = 목표높이 + 여유(빈공간 최소화). 대구는 1.5배 확대 요청 반영.
_SMALL_BUILD = {"세종특별자치시": 660.0, "대전광역시": 720.0}   # 대구 제거(2026-06-09, 타 시도와 통일)
_SMALL_VIEW_PAD = 160.0   # viewBox = target + 여유(도넛/제목 공간)
# 경기 북/남 분할(자치구별 own bbox 정규화 → 확대). 클릭 href는 '경기도' 유지.
_GG_NORTH = {"연천군", "포천시", "동두천시", "양주시", "파주시", "의정부시", "가평군",
             "고양시덕양구", "고양시일산동구", "고양시일산서구",
             "김포시", "구리시", "남양주시", "양평군"}


def _iter_rings(geom):
    if geom["type"] == "Polygon":
        for ring in geom["coordinates"]:
            yield ring
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            for ring in poly:
                yield ring


def _centroid(pts):
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _ring_area(ring):
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return abs(s) * 0.5


def _feat_bbox(f):
    xs = [p[0] for r in _iter_rings(f["geometry"]) for p in r]
    ys = [p[1] for r in _iter_rings(f["geometry"]) for p in r]
    return min(xs), max(xs), min(ys), max(ys)


_INTRA_MAINLAND_FRAC = 0.15   # main ring 면적의 이 비율 이상 = '제2 본토'(섬 아님) → 당기지 않음.
#   안산시단원구처럼 본토가 2개 폴리곤(시화호로 분단)일 때 동쪽 본토를 섬으로 오인해 서쪽으로
#   당겨 인접 시흥시와 겹치는 버그 방지(2026-06-14). 진짜 부속섬(대부도 등)은 main의 <4%라 영향 없음.


def _intra_compact(f, gap):
    """A. feature 내부: 본체(최대 ring) bbox에서 멀리 떨어진 부속섬 ring을 당김.
    단, main 면적의 _INTRA_MAINLAND_FRAC 이상인 ring = 분단된 본토로 보고 당기지 않음."""
    rings = [(r, _ring_area(r), _centroid(r)) for r in _iter_rings(f["geometry"])]
    if len(rings) < 2:
        return
    main_area = max(r[1] for r in rings)
    main = max(rings, key=lambda r: r[1])[0]
    mx = [p[0] for p in main]; my = [p[1] for p in main]
    Lx0, Lx1, Ly0, Ly1 = min(mx), max(mx), min(my), max(my)
    bx, by = (Lx1 - Lx0) * 0.06, (Ly1 - Ly0) * 0.06
    for ring, area, (ccx, ccy) in rings:
        if ring is main:
            continue
        if main_area > 0 and area >= main_area * _INTRA_MAINLAND_FRAC:
            continue                           # 분단된 본토(섬 아님) → 위치 유지
        dx = dy = 0.0
        if ccx < Lx0 - bx:   dx = (Lx0 - bx - ccx) * (1 - gap)
        elif ccx > Lx1 + bx: dx = (Lx1 + bx - ccx) * (1 - gap)
        if ccy < Ly0 - by:   dy = (Ly0 - by - ccy) * (1 - gap)
        elif ccy > Ly1 + by: dy = (Ly1 + by - ccy) * (1 - gap)
        if dx or dy:
            for p in ring:
                p[0] += dx; p[1] += dy


def _move_island_feature(f, scale, direction, main_bbox):
    """B. 섬 자치구를 자기 중심 기준 scale 축소 후 본토 bbox 옆으로 이동."""
    pts = [p for r in _iter_rings(f["geometry"]) for p in r]
    fcx, fcy = _centroid(pts)
    # 축소
    for r in _iter_rings(f["geometry"]):
        for p in r:
            p[0] = fcx + (p[0] - fcx) * scale
            p[1] = fcy + (p[1] - fcy) * scale
    # 축소 후 feature bbox
    fx0, fx1, fy0, fy1 = _feat_bbox(f)
    Lx0, Lx1, Ly0, Ly1 = main_bbox
    pad_x = (Lx1 - Lx0) * 0.03; pad_y = (Ly1 - Ly0) * 0.03
    tx = ty = None
    if "W" in direction:   tx = Lx0 - pad_x - fx1          # 본토 왼쪽에 붙임
    elif "E" in direction: tx = Lx1 + pad_x - fx0
    if "N" in direction:   ty = Ly1 + pad_y - fy0          # 본토 위쪽
    elif "S" in direction: ty = Ly0 - pad_y - fy1
    for r in _iter_rings(f["geometry"]):
        for p in r:
            if tx is not None: p[0] += tx
            if ty is not None: p[1] += ty


def _local_scale(f, factor):
    """C. 자치구를 자기 중심 기준 확대(소형 자치구 클릭성)."""
    pts = [p for r in _iter_rings(f["geometry"]) for p in r]
    cx, cy = _centroid(pts)
    for r in _iter_rings(f["geometry"]):
        for p in r:
            p[0] = cx + (p[0] - cx) * factor
            p[1] = cy + (p[1] - cy) * factor


def _explode(features, members, push, scale):
    """D. 밀집 자치구를 군집 중심에서 밀어내며(push) 각자 확대(scale)."""
    cs = []
    for f in features:
        if f["properties"]["name"] in members:
            cs.append(_centroid([p for r in _iter_rings(f["geometry"]) for p in r]))
    if not cs:
        return
    ccx = sum(c[0] for c in cs) / len(cs); ccy = sum(c[1] for c in cs) / len(cs)
    for f in features:
        nm = f["properties"]["name"]
        if nm not in members:
            continue
        fcx, fcy = _centroid([p for r in _iter_rings(f["geometry"]) for p in r])
        dx, dy = (fcx - ccx) * (push - 1), (fcy - ccy) * (push - 1)
        s = scale.get(nm, 1.0)
        for r in _iter_rings(f["geometry"]):
            for p in r:
                p[0] = fcx + (p[0] - fcx) * s + dx
                p[1] = fcy + (p[1] - fcy) * s + dy


def _move_island_abs(f, scale, tlon, tlat):
    """B'. 섬 자치구를 scale 축소 후 centroid를 절대좌표(tlon,tlat)로 이동."""
    pts = [p for r in _iter_rings(f["geometry"]) for p in r]
    fcx, fcy = _centroid(pts)
    for r in _iter_rings(f["geometry"]):
        for p in r:
            p[0] = tlon + (fcx + (p[0] - fcx) * scale - fcx)
            p[1] = tlat + (fcy + (p[1] - fcy) * scale - fcy)


def _pull_mainland(features, islands, keep, west_thresh=None):
    """F. 섬 자치구 ring을 본토 bbox 쪽으로 lon·lat 양방향 압축(형태 유지, 통째 이동)."""
    mx = [p[0] for f in features if f["properties"]["name"] not in islands
          for r in _iter_rings(f["geometry"]) for p in r]
    my = [p[1] for f in features if f["properties"]["name"] not in islands
          for r in _iter_rings(f["geometry"]) for p in r]
    if not mx:
        return
    Lx0, Lx1, Ly0, Ly1 = min(mx), max(mx), min(my), max(my)
    bx, by = (Lx1 - Lx0) * 0.04, (Ly1 - Ly0) * 0.04
    for f in features:
        if f["properties"]["name"] not in islands:
            continue
        for ring in _iter_rings(f["geometry"]):
            ccx = sum(p[0] for p in ring) / len(ring)
            ccy = sum(p[1] for p in ring) / len(ring)
            if west_thresh is not None and ccx >= west_thresh:
                continue                       # 원거리 섬만 선택적으로
            dx = dy = 0.0
            if ccx < Lx0 - bx:   dx = (Lx0 - bx - ccx) * (1 - keep)
            elif ccx > Lx1 + bx: dx = (Lx1 + bx - ccx) * (1 - keep)
            if ccy < Ly0 - by:   dy = (Ly0 - by - ccy) * (1 - keep)
            elif ccy > Ly1 + by: dy = (Ly1 + by - ccy) * (1 - keep)
            if dx or dy:
                for p in ring:
                    p[0] += dx; p[1] += dy


def _move_group(features, members, scale):
    """F'. 섬 그룹을 그룹 중심 기준 균일 축소(상대위치 보존) 후 본토 왼쪽으로 이동."""
    def mpts():
        return [p for f in features if f["properties"]["name"] in members
                for r in _iter_rings(f["geometry"]) for p in r]
    pts = mpts()
    if not pts:
        return
    gcx = sum(p[0] for p in pts) / len(pts); gcy = sum(p[1] for p in pts) / len(pts)
    for f in features:
        if f["properties"]["name"] in members:
            for r in _iter_rings(f["geometry"]):
                for p in r:
                    p[0] = gcx + (p[0] - gcx) * scale
                    p[1] = gcy + (p[1] - gcy) * scale
    mx = [p[0] for f in features if f["properties"]["name"] not in members
          for r in _iter_rings(f["geometry"]) for p in r]
    if not mx:
        return
    pad = (max(mx) - min(mx)) * 0.03
    gx1 = max(p[0] for p in mpts())          # 축소된 그룹 우측 끝
    dx = (min(mx) - pad) - gx1               # 본토 좌측 가장자리로 이동(x만, lat 유지)
    for f in features:
        if f["properties"]["name"] in members:
            for r in _iter_rings(f["geometry"]):
                for p in r:
                    p[0] += dx


def _drop_far_rings(features, name, lon_lt):
    """H. 자치구의 극서단 폴리곤(centroid lon < 기준) 제거 → 그 자리는 바다 배경."""
    for f in features:
        if f["properties"]["name"] != name:
            continue
        g = f["geometry"]
        if g["type"] == "MultiPolygon":
            kept = [poly for poly in g["coordinates"]
                    if (sum(p[0] for p in poly[0]) / len(poly[0])) >= lon_lt]
            if kept:
                g["coordinates"] = kept


def _partial_scale(features, cfg):
    """J. 자치구의 lon>=lon_ge ring만 자기중심 ratio배(나머지 유지)."""
    for nm, c in cfg.items():
        for f in features:
            if f["properties"]["name"] != nm:
                continue
            keep = [r for r in _iter_rings(f["geometry"])
                    if (sum(p[0] for p in r) / len(r)) >= c["lon_ge"]]
            pts = [p for r in keep for p in r]
            if not pts:
                continue
            cx = sum(p[0] for p in pts) / len(pts); cy = sum(p[1] for p in pts) / len(pts)
            for ring in keep:
                for p in ring:
                    p[0] = cx + (p[0] - cx) * c["ratio"]
                    p[1] = cy + (p[1] - cy) * c["ratio"]


def _scale_toward(features, cfg):
    """I. 자치구별 기준점(ref) 향해 균일 축소(거리·크기 ×ratio). lon_lt 지정 시 그 서쪽 ring만."""
    for f in features:
        t = cfg.get(f["properties"]["name"])
        if not t:
            continue
        rlon, rlat = t["ref"]; ratio = t["ratio"]; thr = t["lon_lt"]
        for ring in _iter_rings(f["geometry"]):
            if thr is not None and (sum(p[0] for p in ring) / len(ring)) >= thr:
                continue
            for p in ring:
                p[0] = rlon + (p[0] - rlon) * ratio
                p[1] = rlat + (p[1] - rlat) * ratio


def _compact_islands(features, sido_name):
    """H(극서단제거)→J(부분축소)→I(거리축소)→A→B→C→D→F→F'. in-place."""
    for nm, lon_lt in _DROP_FAR.get(sido_name, {}).items():
        _drop_far_rings(features, nm, lon_lt)
    if sido_name in _PARTIAL_SCALE:
        _partial_scale(features, _PARTIAL_SCALE[sido_name])
    if sido_name in _SCALE_TOWARD:
        _scale_toward(features, _SCALE_TOWARD[sido_name])
    gap = _INTRA_GAP.get(sido_name)
    if gap is not None:
        for f in features:
            _intra_compact(f, gap)
    pm = _PULL_MAINLAND.get(sido_name)
    if pm:
        _pull_mainland(features, pm["islands"], pm["keep"], pm.get("west_thresh"))
    mg = _MOVE_GROUP.get(sido_name)
    if mg:
        _move_group(features, mg["members"], mg["scale"])
    for nm, (sc, tlon, tlat) in _MOVE_ABS.get(sido_name, {}).items():
        for f in features:
            if f["properties"]["name"] == nm:
                _move_island_abs(f, sc, tlon, tlat)
    for nm, fac in _LOCAL_SCALE.get(sido_name, {}).items():
        for f in features:
            if f["properties"]["name"] == nm:
                _local_scale(f, fac)
    exp = _EXPLODE.get(sido_name)
    if exp:
        _explode(features, exp["members"], exp["push"], exp["scale"])
    moves = _MOVE_ISLAND.get(sido_name)
    if moves:
        # 본토 = 이동 대상 외 feature 들의 bbox
        main_pts_x, main_pts_y = [], []
        for f in features:
            if f["properties"]["name"] in moves:
                continue
            for r in _iter_rings(f["geometry"]):
                for p in r:
                    main_pts_x.append(p[0]); main_pts_y.append(p[1])
        main_bbox = (min(main_pts_x), max(main_pts_x), min(main_pts_y), max(main_pts_y))
        for f in features:
            mv = moves.get(f["properties"]["name"])
            if mv:
                _move_island_feature(f, mv[0], mv[1], main_bbox)


def _build_one(features, water=None, hull_names=None, small=False):
    """한 시도의 시군구 feature 리스트 → {viewBox,width,height,paths,outline,rivers}.
    hull_names: 군단위 섬+바다 클릭영역(convex hull) 부여할 자치구명 set.
    small=True: 자치구 본체만 축소·중앙배치(도넛/라벨은 map_inline에서 정상 폼 유지)."""
    hull_names = hull_names or set()
    # mercator + bounds
    merc_feats = []
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    latmin, latmax = float("inf"), float("-inf")   # 수면 클리핑용 lon/lat bbox
    for f in features:
        rings = []
        for ring in rings_of(f["geometry"]):
            pts = [merc(lon, lat) for lon, lat in ring]
            rings.append(pts)
            for (lon, lat), (x, y) in zip(ring, pts):
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
                latmin, latmax = min(latmin, lat), max(latmax, lat)
        merc_feats.append((f["properties"]["name"], f["properties"]["code"], rings))

    span_x, span_y = maxx - minx, maxy - miny
    scale = (WIDTH - 2 * PAD) / span_x
    height = span_y * scale + 2 * PAD

    def tx(x): return round((x - minx) * scale + PAD, PREC)
    def ty(y): return round((maxy - y) * scale + PAD, PREC)

    from shapely.geometry import MultiPoint
    paths = []
    for name, code, rings in merc_feats:
        d_parts = []
        feat_px = []
        for ring in rings:
            px = [(tx(x), ty(y)) for x, y in ring]
            feat_px.extend(px)
            xs = [p[0] for p in px]; ys = [p[1] for p in px]
            if (max(xs) - min(xs)) < MIN_RING_PX and (max(ys) - min(ys)) < MIN_RING_PX:
                continue
            simp = rdp(px, EPS)
            if len(simp) < 3:
                continue
            seg = [f"M{simp[0][0]} {simp[0][1]}"] + [f"L{x} {y}" for x, y in simp[1:]] + ["Z"]
            d_parts.append("".join(seg))
        if d_parts:
            # 2013 행정구역명 → 현재 명칭 보정 (raw GeoJSON이 구 명칭)
            nm = _RENAME_BY_CODE.get(code, name)
            rec = {"name": nm, "code": code, "d": "".join(d_parts)}
            if nm in hull_names and len(feat_px) >= 3:
                try:
                    hc = list(MultiPoint(feat_px).convex_hull.exterior.coords)
                    rec["hull"] = "M" + "L".join(f"{round(x,1)} {round(y,1)}"
                                                 for x, y in hc) + "Z"
                except Exception:
                    pass
            paths.append(rec)

    # 시도 외곽 union (해안/외곽 두께용)
    from shapely.geometry import Polygon as ShPoly
    from shapely.ops import unary_union
    shapes = []
    for f in features:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            try:
                sp = ShPoly(poly[0], poly[1:])
                if sp.is_valid and sp.area > 0:
                    shapes.append(sp)
            except Exception:
                pass
    outline = []
    if shapes:
        union = unary_union(shapes).buffer(0)
        ups = [union] if union.geom_type == "Polygon" else list(union.geoms)
        for poly in ups:
            rings = [list(poly.exterior.coords)] + [list(h.coords) for h in poly.interiors]
            d_parts = []
            for ring in rings:
                px = [(tx(merc(lon, lat)[0]), ty(merc(lon, lat)[1])) for lon, lat in ring]
                xs = [p[0] for p in px]; ys = [p[1] for p in px]
                if (max(xs) - min(xs)) < MIN_RING_PX and (max(ys) - min(ys)) < MIN_RING_PX:
                    continue
                simp = rdp(px, EPS)
                if len(simp) < 3:
                    continue
                seg = [f"M{simp[0][0]} {simp[0][1]}"] + [f"L{x} {y}" for x, y in simp[1:]] + ["Z"]
                d_parts.append("".join(seg))
            if d_parts:
                outline.append("".join(d_parts))

    # minx/maxx = merc x = lon (merc x==lon). lat bbox 는 위에서 추적.
    water_paths = _project_water(water, minx, maxx, latmin, latmax, tx, ty) if water else []

    # 소형 시도: 자치구 본체만 목표높이(small)로 축소+중앙배치, viewBox는 target+여유
    if small:
        view_h = small + _SMALL_VIEW_PAD
        s = small / height
        sx = (WIDTH - WIDTH * s) / 2          # 가로 중앙
        sy = (view_h - height * s) / 2         # 세로 중앙
        paths = [{**p, "d": _scale_path_d(p["d"], s, sx, sy),
                  **({"hull": _scale_path_d(p["hull"], s, sx, sy)} if p.get("hull") else {})}
                 for p in paths]
        outline = [_scale_path_d(o, s, sx, sy) for o in outline]
        height = view_h

    return {"viewBox": f"0 0 {round(WIDTH,1)} {round(height,1)}",
            "width": WIDTH, "height": round(height, 1),
            "paths": paths, "outline": outline, "water": water_paths}


def main():
    muni = json.load(open(SIGUNGU, encoding="utf-8"))
    prov = json.load(open(PROV, encoding="utf-8"))
    code2name = {f["properties"]["code"]: f["properties"]["name"] for f in prov["features"]}
    water = _load_water()
    print(f"수면 폴리곤(강+호수): {len(water)}개 로드")

    by_sido = defaultdict(list)
    for f in muni["features"]:
        sido_code = str(f["properties"]["code"])[:2]
        by_sido[sido_code].append(f)

    result = {}
    for sido_code, feats in by_sido.items():
        sido_name = code2name.get(sido_code)
        if not sido_name:
            print(f"  ⚠️ 시도명 매핑 실패 code={sido_code} ({len(feats)}구) — 스킵")
            continue
        # 섬 당기기(compaction) — 내부 압축 + 섬 군 이동(해당 시도만, 나머지 no-op)
        _compact_islands(feats, sido_name)
        hull_names = _HULL_HIT.get(sido_name)
        # 한강(수면)은 서울 지도에만 표시. 나머지 시도는 강 미표시.
        result[sido_name] = _build_one(feats, water if sido_name == "서울특별시" else None,
                                       hull_names=hull_names,
                                       small=_SMALL_BUILD.get(sido_name))
        print(f"  {sido_name:10s} 시군구 {len(result[sido_name]['paths']):2d}개 "
              f"수면 {len(result[sido_name]['water']):3d}개 viewBox={result[sido_name]['viewBox']}")
        # 경기도 = 북/남 분할 추가(각자 own bbox 정규화 → 확대). 클릭 href는 '경기도'.
        if sido_name == "경기도":
            north = [f for f in feats if f["properties"]["name"] in _GG_NORTH]
            south = [f for f in feats if f["properties"]["name"] not in _GG_NORTH]
            result["경기도(북)"] = _build_one(north)
            result["경기도(남)"] = _build_one(south)
            print(f"    └ 경기 분할: 북 {len(north)}구 / 남 {len(south)}구")

    json.dump(result, open(DST, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\n💾 저장: {DST} ({os.path.getsize(DST):,} bytes, {len(result)}개 시도)")


if __name__ == "__main__":
    main()
