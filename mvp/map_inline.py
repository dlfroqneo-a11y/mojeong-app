# -*- coding: utf-8 -*-
"""
모정 — 대한민국 지도 (인라인 SVG, iframe 미사용).
레이어(맨 앞→뒤 = paint 아래→위):
  1) hit:    투명 클릭/hover 감지 (a href) + 왼쪽 라벨. 맨 앞이라 CSS `~`로 뒤 레이어 모두 제어.
  2) sidebase: 각 시도 측면 두께(개별). 3) topbase: 평면 윗면. 4) overlay: 떠오른 블록.
개선(2026-06-02):
  · _LIFT 8(떠오름 높이 절반) — 전국·시군구 동일 적용.
  · hover 도넛이 빈 우측 공간에 '기본 패널'로 상주(hover 전 전체 요약). 같은 배경 seamless.
  · 선택 시 면 색(데이터 색) 유지 — 어두운 fill로 덮지 않고 외곽선만 강조.
  · build_emd_image(): 자치구 L2 화면용 행정동(읍면동) 경계 = 클릭 불가 지도 이미지.
클릭: <a href="?sido=..."> 페이지 이동. iframe 미사용 → 활성창 이벤트 차단 회피.
"""
import json
import math
import os
import re
from urllib.parse import quote

_DIR = os.path.dirname(os.path.abspath(__file__))
_SVG = os.path.join(_DIR, "geo", "sido_svg.json")
_SVG_SIGUNGU = os.path.join(_DIR, "geo", "sigungu_svg.json")
_EMD = os.path.join(_DIR, "geo", "emd_svg.json")
_OFFSETS = os.path.join(_DIR, "geo", "island_offsets.json")
_OFFSETS_CACHE = None


def _island_offsets(sido):
    """섬 드래그 에디터(make_island_editor) 결과 = {시도:{자치구:{섬번호:{dx,dy,scale}}}}."""
    global _OFFSETS_CACHE
    if _OFFSETS_CACHE is None:
        try:
            with open(_OFFSETS, encoding="utf-8") as f:
                _OFFSETS_CACHE = json.load(f)
        except Exception:
            _OFFSETS_CACHE = {}
    return _OFFSETS_CACHE.get(sido, {})


def _apply_offsets(d, gu_off):
    """자치구 path의 각 subpath(섬)에 {dx,dy,scale} 적용(중심기준 scale + 평행이동)."""
    if not gu_off:
        return d
    out = []
    for i, sub in enumerate(re.findall(r"M[^M]+", d)):
        off = gu_off.get(str(i))
        if not off:
            out.append(sub); continue
        if off.get("hide"):
            continue                       # 실제 섬 아님(경계 구멍 등) → 렌더 제외
        dx, dy, s = off.get("dx", 0), off.get("dy", 0), off.get("scale", 1)
        nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", sub)]
        cx = sum(nums[0::2]) / len(nums[0::2]); cy = sum(nums[1::2]) / len(nums[1::2])

        def tf(m):
            x, y = float(m.group(2)), float(m.group(3))
            return f"{m.group(1)}{round(cx + (x - cx) * s + dx, 1)} {round(cy + (y - cy) * s + dy, 1)}"
        out.append(re.sub(r"([ML])(-?\d+\.?\d*) (-?\d+\.?\d*)", tf, sub))
    return "".join(out)

def _zoom_path(d, cx, cy, s, tx, ty):
    """경로 d의 모든 M/L 좌표를 (cx,cy) 기준 s배 확대 후 (tx,ty) 평행이동."""
    def tf(m):
        x, y = float(m.group(2)), float(m.group(3))
        return f"{m.group(1)}{round(cx + (x - cx) * s + tx, 1)} {round(cy + (y - cy) * s + ty, 1)}"
    return re.sub(r"([ML])(-?\d+\.?\d*) (-?\d+\.?\d*)", tf, d)


_PAD_L = 290   # 좌측 여백(2026-06-09 210→290: 지도 좌치우침 완화 + 좌측 라벨 잘림 방지)
_PAD_R = 360
# 시도별 좌/우 여백 override (viewBox 프레이밍용). 비우면 전 시도 기본값.
_PAD_OVERRIDE = {}
# 시도별 '지도만' 확대: (scale, target_right_x). viewBox(도넛·글씨)는 불변, 자치구 도형 좌표만 scale.
# target_right_x = 확대 후 지도 우측끝이 닿을 x(도넛 좌측 직전). 인천=섬이 동쪽에 몰려 작게 보임 → 지도만 키움.
# 섬 offset(에디터 배치)로 content가 폭의 일부만 차지하는 시도 → 다른 시도처럼 '폭 1000 채우기'로
# 재정규화. viewBox 공식·도넛 크기가 전 시도 동일해져 일관성 유지(인천만 특별취급 방지).
# 세종·대전(2026-06-11 추가): sigungu 좌표가 0~1000 정규화 안 됨(maxx 727/783) → 고정 도넛(cx=1185,
#   우측 1305)이 viewBox(maxx+360) 밖으로 잘림. 폭 1000 정규화로 도넛이 우측 여백에 정상 안착.
_MAP_ZOOM = {"인천광역시", "세종특별자치시", "대전광역시"}
# 시도별 '블록만 축소'(viewBox·도넛·라벨 불변, 자치구 도형만 중심기준 scale → 여백). 대구=0.8배.
_BLOCK_SCALE = {"대구광역시": 0.8}
_DEPTH = 8       # 입체 겹 수(2026-06-10 16→8: SVG ~45%↓ 렌더 가속. _STEP·_DX 2배로 총깊이 동일 유지)
_STEP = 1.6
_DX = 0.24
_LIFT = 2       # 떠오름 높이(2026-06-09 다시 절반 4→2)
_DOKDO_SIDO = "경상북도"


def _darken(hexcol, k=0.78):
    h = hexcol.lstrip("#")
    if len(h) != 6:
        return hexcol
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    f = lambda v: max(0, min(255, int(v * k)))
    return f"#{f(r):02X}{f(g):02X}{f(b):02X}"


def _shade(t):
    a = (0xD2, 0xDA, 0xE2)
    b = (0xB2, 0xBD, 0xC9)
    f = lambda i: int(a[i] + (b[i] - a[i]) * t)
    return f"#{f(0):02X}{f(1):02X}{f(2):02X}"


def _stack(d):
    out = []
    for i in range(_DEPTH, 0, -1):
        col = _shade(i / _DEPTH)
        out.append(f'<path fill="{col}" transform="translate({round(_DX*i,2)},{round(_STEP*i,2)})" d="{d}"/>')
    return "".join(out)


_STYLE_BASE = """
<style>
.mjmap { background:#EAF4FB; border-radius:10px; padding:10px 6px; text-align:center; }
.mjmap svg { height:800px; width:auto; max-width:100%; display:inline-block; overflow:visible; }
/* 시군구 지도: 폭 100%로 채우고 높이는 viewBox 비율 자동 → 상하 레터박스 여백 제거 */
.mjmapsg svg { width:100%; height:auto; max-height:820px; }
.mjmap a { text-decoration:none; }
.mjmap .hitp { fill:transparent; stroke:transparent; stroke-width:22; stroke-linejoin:round;
  pointer-events:all; cursor:pointer; }
.mjmap .hullp { fill:transparent; pointer-events:all; cursor:pointer; }
.mjmap .poplbl { font-size:23px; font-weight:800; fill:#3A4754; pointer-events:none;
  paint-order:stroke; stroke:#FFFFFF; stroke-width:3px; }
.mjmap .wpoly { fill:#8FC1E8; fill-rule:evenodd; stroke:#6FAAD9; stroke-width:0.6;
  pointer-events:none; opacity:.96; }
.mjmap .sidebase path, .mjmap .topbase path, .mjmap .ov path { stroke:none; pointer-events:none; }
.mjmap .sb { transition: opacity .14s; }
.mjmap .topbase path.tb { fill:#FFFFFF; stroke:#5E6B78; stroke-width:2.4;
  stroke-linejoin:round; transition: opacity .14s; }
/* 선택 시에도 면 색(데이터 색) 유지 → 어두운 fill 제거, 외곽선만 강조 */
.mjmap .topbase path.tb.selected { stroke:#2C5F6F; stroke-width:4; }
.mjmap .ov { opacity:0; pointer-events:none; transform:translateY(0);
  transition: transform .18s ease, opacity .14s; }
.mjmap .ov path.topov { fill:#E3ECF4; stroke:#2C5F6F; stroke-width:1.4; }
.mjmap .ov.show { opacity:1; transform:translateY(-1.5px); }
.mjmap .ov.show path.topov { stroke:#2C5F6F; stroke-width:2.4; }
.mjmap .lbl { opacity:0; font-size:34px; font-weight:800; fill:#1F4654;
  pointer-events:none; transition:opacity .15s; text-anchor:start; letter-spacing:-2px;
  paint-order:stroke; stroke:#FFFFFF; stroke-width:5px; }
.mjmap .mk-dot { fill:#52606D; stroke:#fff; stroke-width:1.5; }
.mjmap .mk-lbl { font-size:30px; font-weight:700; fill:#1F4654; cursor:pointer; }
/* 행정동 경계 비클릭 이미지 */
.mjemd { background:#F4F8FB; border:1px solid #E1E8EE; border-radius:10px; padding:6px; text-align:center; }
.mjemd svg { width:100%; height:auto; max-height:300px; display:inline-block; }
.mjemd path { fill:#FFFFFF; stroke:#9AA5B1; stroke-width:1.1; stroke-linejoin:round; }
.mjemd text { font-weight:700; fill:#3A4754; pointer-events:none;
  text-anchor:middle; paint-order:stroke; stroke:#FFFFFF; stroke-width:4px; }
</style>
"""


def _fmt_eok(e):
    return f"{e/10000:.1f}조" if e >= 10000 else f"{e:,.0f}억"


def _donut(cx, cy, r, pct, name, budget_eok, exec_eok, pop, idx, subtitle=""):
    C = 2 * math.pi * r
    pct = max(0.0, min(100.0, pct))
    fg = round(C * pct / 100, 1)
    col = "#2E9E5B" if pct >= 80 else ("#E8743B" if pct >= 50 else "#D64545")
    pop_txt = (f'<text x="{cx}" y="{cy+r+120}" text-anchor="middle" font-size="30" '
               f'font-weight="700" fill="#52606D">인구 {pop:,}명</text>') if pop else ""
    # 부제(전국 도넛 전용) — 이름 아래 작은 회색 줄
    name_y = cy - r - (52 if subtitle else 34)
    sub_txt = (f'<text x="{cx}" y="{cy-r-18}" text-anchor="middle" font-size="19" '
               f'fill="#9AA5B1">{subtitle}</text>') if subtitle else ""
    return (
        f'<g class="rinfo rinfo-{idx}">'
        f'<text x="{cx}" y="{name_y}" text-anchor="middle" font-size="42" font-weight="800" fill="#1F4654">{name}</text>'
        f'{sub_txt}'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#E1E8EE" stroke-width="26"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}" stroke-width="26" '
        f'stroke-dasharray="{fg} {C:.1f}" stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>'
        f'<text x="{cx}" y="{cy-22}" text-anchor="middle" font-size="24" fill="#7B8794">총예산</text>'
        f'<text x="{cx}" y="{cy+14}" text-anchor="middle" font-size="40" font-weight="800" fill="#2C5F6F">{_fmt_eok(budget_eok)}</text>'
        f'<text x="{cx}" y="{cy+r+46}" text-anchor="middle" font-size="32" font-weight="700" fill="{col}">집행 {_fmt_eok(exec_eok)}</text>'
        f'<text x="{cx}" y="{cy+r+84}" text-anchor="middle" font-size="30" font-weight="800" fill="{col}">집행률 {pct:.0f}%</text>'
        f'{pop_txt}'
        f'</g>'
    )


def _info_layer(paths, stats, H, default_name="전국 기초자치단체", subtitle="", total=None):
    """hover 도넛 + 빈공간 기본 패널(전체 요약). total 주면 기본 패널 합계를 그 값으로(중복합 방지).
    subtitle: 기본 패널(전국) 이름 아래 부제(전국 도넛에만)."""
    cx, r = 1185, 120
    cy = round(H * 0.44, 1)
    items, rules = [], []
    tb = te = tp = 0.0
    for i, p in enumerate(paths):
        s = stats.get(p["name"])
        if not s:
            continue
        tb += s.get("예산_억", 0); te += s.get("집행_억", 0); tp += s.get("인구", 0)
        items.append(_donut(cx, cy, r, s.get("집행률", 0), s.get("표시명") or p["name"],
                            s.get("예산_억", 0), s.get("집행_억", 0), s.get("인구", 0), i))
        rules.append(f'.mjmap .hit-{i}:hover ~ .infowrap .rinfo-{i}{{opacity:1}}')
        rules.append(f'.mjmap .hit-{i}:hover ~ .infowrap .rinfo-default{{opacity:0}}')
    if total:
        tb = total.get("예산_억", tb); te = total.get("집행_억", te)
    trate = (te / tb * 100) if tb else 0
    default_card = _donut(cx, cy, r, trate, default_name, tb, te, int(tp), "default", subtitle=subtitle)
    svg = f'<g class="infowrap">{default_card}{"".join(items)}</g>'
    css = (".mjmap .rinfo{opacity:0;transition:opacity .15s;pointer-events:none}"
           ".mjmap .rinfo-default{opacity:1}" + "".join(rules))
    return svg, css


def _ring(cx, cy, r, frac, col, track="#E1E8EE", w=22):
    C = 2 * math.pi * r
    fg = round(C * max(0.0, min(1.0, frac)), 1)
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{track}" stroke-width="{w}"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}" stroke-width="{w}" '
            f'stroke-dasharray="{fg} {C:.1f}" stroke-linecap="round" transform="rotate(-90 {cx} {cy})"/>')


def _txt(cx, y, s, size, color, weight=400):
    return f'<text x="{cx}" y="{y}" text-anchor="middle" font-size="{size}" font-weight="{weight}" fill="{color}">{s}</text>'


def _combined_block(cx, name, b, s, H):
    y_title = round(H * 0.13, 1)
    cyt, rt = round(H * 0.36, 1), 86
    cyb, rb = round(H * 0.70, 1), 86
    parts = [_txt(cx, y_title, name, 40, "#1F4654", 800)]
    if b:
        rate = b.get("집행률", 0)
        col = "#2E9E5B" if rate >= 80 else ("#E8743B" if rate >= 50 else "#D64545")
        parts += [
            _txt(cx, cyt - rt - 22, "예산 · 집행", 24, "#7B8794"),
            _ring(cx, cyt, rt, rate / 100, col),
            _txt(cx, cyt - 4, _fmt_eok(b.get("예산_억", 0)), 36, "#2C5F6F", 800),
            _txt(cx, cyt + 26, "총예산", 22, "#9AA5B1"),
            _txt(cx, cyt + rt + 36, f"집행 {_fmt_eok(b.get('집행_억',0))} · {rate:.0f}%", 28, col, 700),
        ]
    if s:
        sc = s.get("점수", 0)
        parts += [
            _txt(cx, cyb - rb - 22, "살기좋은 내 동네", 24, "#7B8794"),
            _ring(cx, cyb, rb, sc / 5, "#F98A77", "#F1E3DF"),
            _txt(cx, cyb + 6, f"{sc:.1f}", 48, "#E8743B", 800),
            _txt(cx, cyb + 34, "/ 5점", 22, "#9AA5B1"),
            _txt(cx, cyb + rb + 36, f"만족도 투표 {s.get('참여',0):,}명 참여", 28, "#52606D", 700),
        ]
    return "".join(parts)


def _combined_info_layer(paths, stats, sat, H, default_name="서울특별시 전체", total=None):
    cx = 1185
    items, rules = [], []
    tb = te = 0.0
    ssum = scnt = 0
    for i, p in enumerate(paths):
        name = p["name"]
        b = stats.get(name) if stats else None
        s = sat.get(name) if sat else None
        if not b and not s:
            continue
        if b:
            tb += b.get("예산_억", 0); te += b.get("집행_억", 0)
        if s:
            ssum += s.get("점수", 0) * max(s.get("참여", 0), 1); scnt += max(s.get("참여", 0), 1)
        items.append(f'<g class="rinfo rinfo-{i}">{_combined_block(cx, name, b, s, H)}</g>')
        rules.append(f'.mjmap .hit-{i}:hover ~ .infowrap .rinfo-{i}{{opacity:1}}')
        rules.append(f'.mjmap .hit-{i}:hover ~ .infowrap .rinfo-default{{opacity:0}}')
    if total:
        tb = total.get("예산_억", tb); te = total.get("집행_억", te)
    db = {"예산_억": tb, "집행_억": te, "집행률": (te / tb * 100) if tb else 0}
    ds = {"점수": (ssum / scnt) if scnt else 0, "참여": int(scnt)}
    default_card = f'<g class="rinfo rinfo-default">{_combined_block(cx, default_name, db, ds, H)}</g>'
    svg = f'<g class="infowrap">{default_card}{"".join(items)}</g>'
    css = (".mjmap .rinfo{opacity:0;transition:opacity .15s;pointer-events:none}"
           ".mjmap .rinfo-default{opacity:1}" + "".join(rules))
    return svg, css


def _load():
    with open(_SVG, encoding="utf-8") as f:
        return json.load(f)


_SIGUNGU_CACHE = None
_EMD_CACHE = None


# 소형(단일/소수 자치구) 시도 = 지도 축소. 독도 마커(울릉군 옆 동적 배치).
_SMALL_SIDO = {"세종특별자치시", "대전광역시", "대구광역시"}
_DOKDO_MARK = {"경상북도": {"ref_gu": "울릉군", "name": "독도", "gu": "울릉군", "dx": 70}}


def _path_bbox(d):
    import re as _re
    nums = [float(x) for x in _re.findall(r"-?\d+\.?\d*", d)]
    xs, ys = nums[0::2], nums[1::2]
    return min(xs), max(xs), min(ys), max(ys)


# 서울 한강 표현: 물 폴리곤 제거 + 강남(이남) 11구를 아래로 띄워 그 틈(배경)=한강.
_SEOUL_SOUTH = {"양천구", "강서구", "구로구", "금천구", "영등포구", "동작구",
                "관악구", "서초구", "강남구", "송파구", "강동구"}
_SEOUL_RIVER_GAP = 50.0      # 강남 구를 아래로 이동(≈한강 폭). 조절 시 이 값만.


def _shift_y(d, dy):
    """경로 d의 모든 M/L y좌표를 dy만큼 이동(x 불변)."""
    return re.sub(r"([ML])(-?\d+\.?\d*) (-?\d+\.?\d*)",
                  lambda m: f"{m.group(1)}{m.group(2)} {round(float(m.group(3)) + dy, 1)}", d)


def build_sigungu(sido, selected=None, stats=None, fills=None, sat=None,
                  total=None, href_sido=None):
    """선택 시도의 시군구 클릭 지도(전국 지도와 동일 3D, 떠오름 절반). 클릭 → ?gu=.
    total={예산_억,집행_억}: 기본 패널 합계를 자치구 dedup 합으로(일반구 중복합 방지).
    href_sido: 클릭 href용 시도명(경기 북/남 분할 시 '경기도'로 고정)."""
    global _SIGUNGU_CACHE
    if _SIGUNGU_CACHE is None:
        with open(_SVG_SIGUNGU, encoding="utf-8") as f:
            _SIGUNGU_CACHE = json.load(f)
    d = _SIGUNGU_CACHE.get(sido)
    if not d:
        return None
    link_sido = href_sido or sido
    H = d["height"]
    M = 55                                     # 상하 여백(viewBox 단위) — 잘림 방지 + 약간의 여백
    offsets = _island_offsets(link_sido)        # 섬 드래그 에디터 배치 적용

    _dd = {p["name"]: _apply_offsets(p["d"], offsets.get(p["name"])) for p in d["paths"]}
    if sido == "서울특별시":          # 한강 이남 구를 아래로 띄움 → 틈(배경)=한강
        _dd = {k: (_shift_y(v, _SEOUL_RIVER_GAP) if k in _SEOUL_SOUTH else v)
               for k, v in _dd.items()}

    # 섬 offset로 content가 폭의 일부만 차지하는 시도 → 폭 0~1000 채우도록 재정규화(타 시도와 동일).
    # 그래야 아래 viewBox 공식이 타 시도와 같은 비율을 내고, 도넛(cx=1185,r=120) 크기도 동일해짐.
    if sido in _MAP_ZOOM:
        _bx, _by = [], []
        for _v in _dd.values():
            _n = [float(x) for x in re.findall(r"-?\d+\.?\d*", _v)]
            _bx += _n[0::2]; _by += _n[1::2]
        if _bx and (max(_bx) - min(_bx)) > 0:
            cminx, cminy = min(_bx), min(_by)
            s = 1000.0 / (max(_bx) - cminx)               # 폭 0~1000 채움
            _dd = {k: _zoom_path(v, cminx, cminy, s, -cminx, -cminy) for k, v in _dd.items()}
            H = round((max(_by) - cminy) * s, 1)          # 정규화 후 높이 → 도넛 cy 등에 사용

    # content bbox(정규화 후) 기준 viewBox = 전 시도 공통 공식
    _ax, _ay = [], []
    for _v in _dd.values():
        _n = [float(x) for x in re.findall(r"-?\d+\.?\d*", _v)]
        _ax += _n[0::2]; _ay += _n[1::2]
    minx, maxx = (min(_ax), max(_ax)) if _ax else (0.0, 1000.0)
    miny, maxy = (min(_ay), max(_ay)) if _ay else (0.0, H)
    _bs = _BLOCK_SCALE.get(sido)
    if _bs:                                    # 블록만 중심기준 축소(viewBox는 원본 bbox 유지 → 여백 생김)
        _ccx, _ccy = (minx + maxx) / 2, (miny + maxy) / 2
        _dd = {k: _zoom_path(v, _ccx, _ccy, _bs, 0, 0) for k, v in _dd.items()}
    padL, padR = _PAD_OVERRIDE.get(sido, (_PAD_L, _PAD_R))
    vb = (f"{round(minx - padL, 1)} {round(miny - M, 1)} "
          f"{round((maxx - minx) + padL + padR, 1)} {round((maxy - miny) + 2 * M, 1)}")
    ycen = round((miny + maxy) / 2, 1)
    lx = round((minx - padL) + ((maxx + padR) - 1305), 1)  # 좌 라벨 좌여백 = 도넛(cx1185+r120=1305) 우여백 대칭

    hullhits = []
    hits, sidebase, topbase, overlay, rules = [], [], [], [], []
    for i, p in enumerate(d["paths"]):
        name = p["name"]
        dd = _dd[name]
        is_sel = (name == selected)
        href = "?sido=" + quote(link_sido) + "&gu=" + quote(name)
        hull = p.get("hull")                  # 군단위 섬+바다 클릭영역
        if hull:
            hullhits.append(f'<a class="hit hit-{i}" href="{href}" target="_self">'
                            f'<path class="hullp" d="{hull}"/></a>')
        hits.append(
            f'<a class="hit hit-{i}" href="{href}" target="_self">'
            f'<path class="hitp" d="{dd}"/>'
            f'<text class="lbl lbl-{i}" x="{lx}" y="{ycen}">{name}</text></a>'
        )
        sidebase.append(f'<g class="sb sb-{i}">{_stack(dd)}</g>')
        tb = "tb selected" if is_sel else "tb"
        fstyle = f' style="fill:{fills[name]}"' if (fills and name in fills) else ""
        topbase.append(f'<path class="{tb} tbid-{i}"{fstyle} d="{dd}"/>')
        ov = f"ov ov-{i} show" if is_sel else f"ov ov-{i}"
        ovfill = _darken(fills[name]) if (fills and name in fills) else _darken("#FFFFFF")
        overlay.append(f'<g class="{ov}">{_stack(dd)}<path class="topov" style="fill:{ovfill}" d="{dd}"/></g>')
        rules.append(f'.mjmap .hit-{i}:hover ~ .sidebase .sb-{i}{{opacity:0}}')
        rules.append(f'.mjmap .hit-{i}:hover ~ .topbase .tbid-{i}{{opacity:0}}')
        rules.append(f'.mjmap .hit-{i}:hover ~ .ov-{i}{{opacity:1;transform:translateY(-{_LIFT}px)}}')
        rules.append(f'.mjmap .hit-{i}:hover .lbl-{i}{{opacity:1}}')
        if is_sel:
            rules.append(f'.mjmap .sb-{i}{{opacity:0}}')

    if sat and stats:
        info_svg, info_css = _combined_info_layer(d["paths"], stats, sat, H,
                                                  default_name=f"{link_sido} 전체", total=total)
    elif stats:
        info_svg, info_css = _info_layer(d["paths"], stats, H,
                                         default_name=f"{link_sido} 전체", total=total)
    else:
        info_svg, info_css = "", ""
    # 독도 마커(경북) — 울릉군 우측에 동적 배치(울릉이 울진 높이로 내려감)
    markerhits = ""
    mk = _DOKDO_MARK.get(sido)
    if mk:
        refp = next((p for p in d["paths"] if p["name"] == mk["ref_gu"]), None)
        if refp:
            rx0, rx1, ry0, ry1 = _path_bbox(refp["d"])
            mxp, myp = round(rx1 + mk["dx"], 1), round((ry0 + ry1) / 2, 1)
            mhref = "?sido=" + quote(link_sido) + "&gu=" + quote(mk["gu"])
            markerhits = (
                f'<a class="mk-dokdo" href="{mhref}" target="_self">'
                f'<circle class="mk-dot" cx="{mxp}" cy="{myp}" r="7"/>'
                f'<text class="mk-lbl" x="{mxp}" y="{myp-14}" text-anchor="middle">{mk["name"]}</text>'
                f'</a>')
    # 서울은 한강 물 폴리곤 제거(강남 구를 띄운 틈=배경으로 한강 표현)
    water = "" if sido == "서울특별시" else \
        "".join(f'<path class="wpoly" d="{wd}"/>' for wd in d.get("water", []))
    svg = (
        f'<div class="mjmap mjmapsg"><svg viewBox="{vb}" preserveAspectRatio="xMidYMid meet">'
        f'{"".join(hullhits)}{"".join(hits)}{markerhits}'
        f'<g class="sidebase">{"".join(sidebase)}</g>'
        f'<g class="topbase">{"".join(topbase)}</g>'
        f'<g class="water">{water}</g>'
        f'{"".join(overlay)}{info_svg}'
        f'</svg></div>'
    )
    dyn = "<style>" + "".join(rules) + info_css + "</style>"
    return _STYLE_BASE + dyn + svg


def build_emd_image(sido, gu, show_labels=True):
    """자치구 L2 화면용 — 행정동(읍면동) 경계 = 클릭 불가 지도 이미지. 없으면 None."""
    global _EMD_CACHE
    if _EMD_CACHE is None:
        try:
            with open(_EMD, encoding="utf-8") as f:
                _EMD_CACHE = json.load(f)
        except FileNotFoundError:
            _EMD_CACHE = {}
    d = _EMD_CACHE.get(f"{sido}|{gu}")
    if not d:
        return None
    W, H = d["w"], d["h"]
    # 행정동 라벨 폰트 = 전 자치구 동일 화면크기(인천 동구 기준). emd는 폭 1000 정규화이나 높이 H가
    # 제각각이고 .mjemd svg{max-height:300px} 캡 때문에 같은 폰트라도 자치구마다 다르게 보임 →
    # 폰트를 H에 비례시켜 캡 스케일(300/H)을 상쇄. 인천 동구(H=637.2)=29(기존 26+3pt) 기준.
    _REF_FONT, _REF_H, _H_BOUND = 29.0, 637.2, 612.0
    fsz = round(_REF_FONT * max(H, _H_BOUND) / _REF_H, 1)
    body = []
    for p in d["paths"]:
        body.append(f'<path d="{p["d"]}"/>')
    if show_labels:
        for lb in d.get("labels", []):
            body.append(f'<text x="{lb["cx"]}" y="{lb["cy"]}" font-size="{fsz}">{lb["name"]}</text>')
    svg = (f'<div class="mjemd"><svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet">'
           f'{"".join(body)}</svg></div>')
    return _STYLE_BASE + svg


def build(selected=None, stats=None, fills=None, labels=None):
    d = _load()
    H = d["height"]
    ycen = round(H / 2, 1)
    lx = round(-_PAD_L + (1000 + _PAD_R) - 1305, 1)  # 좌 라벨 좌여백 = 도넛(cx1185+r120=1305) 우여백 대칭
    vb = f"-{_PAD_L} 0 {1000 + _PAD_L + _PAD_R} {H}"

    hullhits, hits, sidebase, topbase, overlay, rules, poplbls = [], [], [], [], [], [], []
    for i, p in enumerate(d["paths"]):
        dd, name = p["d"], p["name"]
        is_sel = (name == selected)
        href = "?sido=" + quote(name)
        hull = p.get("hull")
        if hull:
            hullhits.append(f'<a class="hit hit-{i}" href="{href}" target="_self">'
                            f'<path class="hullp" d="{hull}"/></a>')
        hits.append(
            f'<a class="hit hit-{i}" href="{href}" target="_self">'
            f'<path class="hitp" d="{dd}"/>'
            f'<text class="lbl lbl-{i}" x="{lx}" y="{ycen}">{name}</text></a>'
        )
        if labels and name in labels:
            poplbls.append(f'<text class="poplbl" x="{p.get("cx",0)}" y="{p.get("cy",0)}" '
                           f'text-anchor="middle">{labels[name]}</text>')
        sidebase.append(f'<g class="sb sb-{i}">{_stack(dd)}</g>')
        tb = "tb selected" if is_sel else "tb"
        fstyle = f' style="fill:{fills[name]}"' if (fills and name in fills) else ""
        topbase.append(f'<path class="{tb} tbid-{i}"{fstyle} d="{dd}"/>')
        ov = f"ov ov-{i} show" if is_sel else f"ov ov-{i}"
        ovfill = _darken(fills[name]) if (fills and name in fills) else _darken("#FFFFFF")
        overlay.append(f'<g class="{ov}">{_stack(dd)}<path class="topov" style="fill:{ovfill}" d="{dd}"/></g>')
        rules.append(f'.mjmap .hit-{i}:hover ~ .sidebase .sb-{i}{{opacity:0}}')
        rules.append(f'.mjmap .hit-{i}:hover ~ .topbase .tbid-{i}{{opacity:0}}')
        rules.append(f'.mjmap .hit-{i}:hover ~ .ov-{i}{{opacity:1;transform:translateY(-{_LIFT}px)}}')
        rules.append(f'.mjmap .hit-{i}:hover .lbl-{i}{{opacity:1}}')
        if is_sel:
            rules.append(f'.mjmap .sb-{i}{{opacity:0}}')

    gbidx = next((i for i, p in enumerate(d["paths"]) if p["name"] == _DOKDO_SIDO), None)
    dokdo_href = "?sido=" + quote(_DOKDO_SIDO)
    markerhits = []
    for m in d.get("markers", []):
        markerhits.append(
            f'<a class="mk-dokdo" href="{dokdo_href}" target="_self">'
            f'<circle class="mk-dot" cx="{m["x"]}" cy="{m["y"]}" r="6"/>'
            f'<text class="mk-lbl" x="{m["x"]}" y="{m["y"]-12}" text-anchor="middle">{m["name"]}</text>'
            f'</a>'
        )
    if markerhits and gbidx is not None:
        rules.append(f'.mjmap .mk-dokdo:hover ~ .sidebase .sb-{gbidx}{{opacity:0}}')
        rules.append(f'.mjmap .mk-dokdo:hover ~ .topbase .tbid-{gbidx}{{opacity:0}}')
        rules.append(f'.mjmap .mk-dokdo:hover ~ .ov-{gbidx}{{opacity:1;transform:translateY(-{_LIFT}px)}}')
        rules.append(f'.mjmap .mk-dokdo:hover ~ .infowrap .rinfo-{gbidx}{{opacity:1}}')
        rules.append(f'.mjmap .mk-dokdo:hover ~ .infowrap .rinfo-default{{opacity:0}}')

    info_svg, info_css = _info_layer(
        d["paths"], stats, H, subtitle="중앙정부·광역 시·도·교육청 예산 별도") if stats else ("", "")
    poplayer = f'<g class="poplbls">{"".join(poplbls)}</g>' if poplbls else ""
    svg = (
        f'<div class="mjmap"><svg viewBox="{vb}" preserveAspectRatio="xMidYMid meet">'
        f'{"".join(hullhits)}{"".join(hits)}{"".join(markerhits)}'
        f'<g class="sidebase">{"".join(sidebase)}</g>'
        f'<g class="topbase">{"".join(topbase)}</g>'
        f'{"".join(overlay)}{poplayer}{info_svg}'
        f'</svg></div>'
    )
    dyn = "<style>" + "".join(rules) + info_css + "</style>"
    return _STYLE_BASE + dyn + svg
