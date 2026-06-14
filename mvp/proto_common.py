# -*- coding: utf-8 -*-
"""네비게이션 재설계 프로토타입 (공통) — 전국 → 시도 → 자치구 드릴 + 분야별 이행 신호등.
MODE="A": 자치구 화면에 동·읍 경계 영역 표시(클릭 비활성·안내).
MODE="B": 동·읍 지도 없이 데이터 집중.

상태 = URL query param 단일 소스(8501 메인앱과 동일).
  (없음)            → 첫 화면(전국)
  ?sido=X           → 시도 화면(L1)
  ?sido=X&gu=Y      → 자치구 화면(L2)
  ?sido=X&gu=Y&dom=Z→ 분야 이행 상세
지도 클릭 = <a href>(실제 히스토리) + popstate 리로드로 브라우저 뒤로가기 자연 동작.
"""
import json
import math
import os
import re
from pathlib import Path
from urllib.parse import quote
import streamlit as st
import streamlit.components.v1 as components

import data_loader as dl
import map_inline as mi

POC = Path(__file__).resolve().parent.parent / "poc_output"
POP_PATH = Path(__file__).resolve().parent / "geo" / "population_sido.json"
SAT_PATH = POC / "satisfaction_seoul.json"
SIG = {"green": "#2E9E5B", "amber": "#E8743B", "red": "#D64545"}
GANGNAM_DONG = ["역삼동", "삼성동", "청담동", "압구정동", "신사동", "논현동", "대치동",
                "도곡동", "개포동", "일원동", "수서동", "세곡동"]


@st.cache_resource   # 순수 JSON 로더(읽기전용) → 세션간 단일객체 공유(복사 제거, 로딩 가속)
def domain_status():
    p = POC / "domain_status_gangnam.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"분야": []}


@st.cache_resource   # 순수 JSON 로더 → 공유
def gangnam_budget():
    return json.loads((POC / "track_budget_gangnam_detail.json").read_text(encoding="utf-8"))


def _l3_for(domain):
    rows = [b for b in gangnam_budget() if (b.get("분야") or "") == domain]
    return sorted(rows, key=lambda x: -x.get("집행_억", 0))[:10]


# 단일자치단체(행정시·자치시): 산하 행정구역이 같은 道/市 재정을 공유 → 합계는 1회만
_SINGLE_TIER = {"제주특별자치도", "세종특별자치시"}


def sido_total(sido):
    """시도의 자치구 dedup 합(예산·집행). 일반구 중복·단일자치단체 중복 방지 →
    전국 hover ↔ 시도 페이지 ↔ 자치구 합이 모두 일치(부모=Σ자식)."""
    gus = all_districts().get(sido, {})
    if sido in _SINGLE_TIER:
        recs = list(gus.values())
        l1 = recs[0]["l1"] if recs else {}
        b, e = l1.get("총예산_억", 0), l1.get("총집행_억", 0)
    else:
        b = sum(r["l1"]["총예산_억"] for r in gus.values())
        e = sum(r["l1"]["총집행_억"] for r in gus.values())
    return {"예산_억": b, "집행_억": e, "집행률": (e / b * 100) if b else 0}


@st.cache_data
def nation_stats():
    """전국 시도별 = 자치구 dedup 합(드릴다운 정합). hover 도넛·기본 패널 공통."""
    pop = population()
    return {sido: {**sido_total(sido), "인구": pop.get(sido, 0)} for sido in pop}


# 전국 지도 레이어(상단 메뉴): 색 기준 + 색 스케일(연→진). 전부 실데이터.
NATION_LAYERS = {
    "인구": {"key": "인구", "a": (0xE6, 0xF6, 0xEA), "b": (0x63, 0xC9, 0x76), "sqrt": True,
            "cap": "색이 진할수록 인구가 많은 시·도 (숲의 대한민국 — 사람이 모일수록 푸르게)"},
    "총예산": {"key": "예산_억", "a": (0xE8, 0xF1, 0xF8), "b": (0x2C, 0x7F, 0xB8), "sqrt": True,
             "cap": "색이 진할수록 자치구 예산 합이 큰 시·도"},
    "집행률": {"key": "집행률", "a": (0xF3, 0xEC, 0xF7), "b": (0x7E, 0x57, 0xC2), "sqrt": False,
             "cap": "색이 진할수록 예산 집행률이 높은 시·도"},
}


@st.cache_data
def nation_fills(metric="인구"):
    """선택 지표 → 시도 choropleth 색. {시도명: '#RRGGBB'}."""
    spec = NATION_LAYERS.get(metric, NATION_LAYERS["인구"])
    ns = nation_stats()
    vals = {k: v.get(spec["key"], 0) for k, v in ns.items()}
    nums = [x for x in vals.values() if x]
    if not nums:
        return {}
    lo, hi = min(nums), max(nums)
    if spec["sqrt"]:
        lo, hi = math.sqrt(lo), math.sqrt(hi)
    a, b = spec["a"], spec["b"]
    KEEP = {"경기도", "서울특별시"} if metric == "인구" else set()

    def col(v, name):
        x = math.sqrt(v) if spec["sqrt"] else v
        t = (x - lo) / (hi - lo) if hi > lo else 0
        if metric == "인구" and name not in KEEP:
            t = min(t + 0.15, 1.0)
        f = lambda i: int(a[i] + (b[i] - a[i]) * t)
        return f"#{f(0):02X}{f(1):02X}{f(2):02X}"
    return {k: col(v, k) for k, v in vals.items() if v}


@st.cache_data
def population():
    return {k: v for k, v in json.loads(POP_PATH.read_text(encoding="utf-8")).items()
            if not k.startswith("_")}


@st.cache_data
def nation_labels():
    """시도 인구 수치 라벨 {시도명: '929만'}."""
    return {k: f"{round(v/10000):,}만" for k, v in population().items()}


# ---- 살기좋은 내 동네 만족도(참여형, 서울) — 캐시 X(투표로 갱신) ----
def satisfaction():
    d = json.loads(SAT_PATH.read_text(encoding="utf-8"))
    return {gu: {"점수": round(v["sum"] / v["count"], 1) if v["count"] else 0,
                 "참여": v["count"]}
            for gu, v in d["scores"].items()}


# 만족도 색 스케일 — 환경변수 SAT_SCHEME로 선택(로컬 색 비교용). 기본=현재(크림→노랑→피치).
_SAT_SCHEMES = {
    "current": [(0.0, (0xFB, 0xF8, 0xE5)), (3.0, (0xED, 0xE0, 0x7A)), (5.0, (0xE5, 0x9A, 0x65))],  # 8502 현재(크림→노랑→피치)
    "green":   [(0.0, (0xEA, 0xF7, 0xE1)), (5.0, (0x2E, 0x7D, 0x32))],                              # 8503 초록 순차(좋을수록 초록)
    "ylgnbu":  [(0.0, (0xFF, 0xFF, 0xD9)), (2.5, (0x41, 0xB6, 0xC4)), (5.0, (0x22, 0x5E, 0xA8))],   # 8504 노랑-청록-남색
    "rdylgn":  [(0.0, (0xD7, 0x30, 0x27)), (2.5, (0xFE, 0xE0, 0x8B)), (5.0, (0x1A, 0x98, 0x50))],   # 8505 빨강-노랑-초록(발산)
    "coral":   [(0.0, (0xED, 0xE0, 0x7A)), (3.0, (0xFF, 0xF3, 0xE6)), (5.0, (0xFB, 0x6F, 0x5A))],    # 노랑(0)→크림(3)→산호(5)
}
_SAT_ANCHORS = _SAT_SCHEMES.get(os.getenv("SAT_SCHEME", "coral"), _SAT_SCHEMES["coral"])


def sat_color(score):
    s = max(0.0, min(5.0, score))
    for i in range(len(_SAT_ANCHORS) - 1):
        s0, c0 = _SAT_ANCHORS[i]
        s1, c1 = _SAT_ANCHORS[i + 1]
        if s <= s1:
            t = (s - s0) / (s1 - s0) if s1 > s0 else 0
            f = lambda j: int(c0[j] + (c1[j] - c0[j]) * t)
            return f"#{f(0):02X}{f(1):02X}{f(2):02X}"
    c = _SAT_ANCHORS[-1][1]
    return f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}"


def seoul_sat_fills():
    """만족도 점수 → 발산형 색(0=파랑~5=빨강). 0.25점 단위 양자화로 구간 또렷."""
    out = {}
    for gu, v in satisfaction().items():
        q = round(v["점수"] / 0.25) * 0.25
        out[gu] = sat_color(q)
    return out


@st.cache_data
def seoul_budget_stats():
    """서울 25구 예산 l1 {구: {예산_억,집행_억,집행률}} (도넛 윗쪽 예산용)."""
    p = POC / "seoul_districts_budget.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    return {gu: {"예산_억": v["총예산_억"], "집행_억": v["총집행_억"], "집행률": v["집행률"]}
            for gu, v in d.items()}


def cast_vote(gu, score):
    """이용자 1~5점 투표 누적 저장."""
    d = json.loads(SAT_PATH.read_text(encoding="utf-8"))
    s = d["scores"].setdefault(gu, {"sum": 0, "count": 0})
    s["sum"] += int(score)
    s["count"] += 1
    d["_seed"] = False
    SAT_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


@st.cache_resource   # 순수 JSON 로더 → 공유
def all_districts():
    """전국 시군구 L1+L2 (collect_all_districts.py)."""
    p = POC / "districts_all.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


_GU_RE = re.compile(r"^(.+?시)(.+구)$")   # 일반구(수원시장안구) → 부모 시


def _parent_gu(name):
    m = _GU_RE.match(name)
    return m.group(1) if m else name


@st.cache_data
def _sigungu_names(sido):
    sg = json.loads((Path(__file__).resolve().parent / "geo" / "sigungu_svg.json")
                    .read_text(encoding="utf-8"))
    return [p["name"] for p in sg.get(sido, {}).get("paths", [])]


def district_rec(sido, geojson_name):
    """지도 시군구명 → 예산 레코드(일반구는 부모 시로 매핑)."""
    ad = all_districts().get(sido, {})
    return ad.get(_parent_gu(geojson_name)) or ad.get(geojson_name)


@st.cache_data
def sigungu_stats(sido):
    """시도의 구·군별 {예산_억, 집행_억, 집행률} (hover 도넛용) — 전국 채움.
    단일자치단체(제주·세종)는 산하 행정구역이 같은 道/市 재정 공유 → 도넛 제목을 '{시도} 전체'로."""
    single = sido in _SINGLE_TIER
    out = {}
    for nm in _sigungu_names(sido):
        rec = district_rec(sido, nm)
        if rec:
            l1 = rec["l1"]
            e = {"예산_억": l1["총예산_억"], "집행_억": l1["총집행_억"], "집행률": l1["집행률"]}
            if single:
                e["표시명"] = f"{sido} 전체"
            out[nm] = e
    return out


# ---- URL 상태 (단일 소스) ----
def _sido(): return st.query_params.get("sido") or None
def _gu(): return st.query_params.get("gu") or None
def _dom(): return st.query_params.get("dom") or None
def _l4(): return st.query_params.get("l4") or None


def _nav(d):
    """네비게이션 = query_params 원자적 1회 설정(from_dict) → history 항목 1개/이동.
    (clear()+개별 set 은 항목을 여러 개 push해 뒤로가기 순서를 꼬이게 함.)"""
    st.query_params.from_dict({k: v for k, v in d.items() if v})


def go_home():
    _nav({})


def go_sido(name):
    _nav({"sido": name})


def go_gu(name):
    _nav({"sido": _sido(), "gu": name})


def goto_district(sido, gu):
    """전국 화면 → 특정 자치구로 바로 이동(풀체인 쇼케이스용)."""
    _nav({"sido": sido, "gu": gu})


def go_dom(name):
    _nav({"sido": _sido(), "gu": _gu(), "dom": name})


def clear_dom():
    _nav({"sido": _sido(), "gu": _gu()})


def _theme(): return st.query_params.get("theme") or None


def go_theme(name):
    """환경·교통 킬러 테마 뷰로 이동(시도 유지, gu/dom/l4 제거)."""
    _nav({"sido": _sido(), "theme": name})


def clear_theme():
    _nav({"sido": _sido()})


def _ai(): return st.query_params.get("ai") or None


def go_ai():
    """🤖 AI 자동 발견(시도별 자치구 시민감시 지표) 화면으로 — 현재 시도 유지."""
    _nav({"sido": _sido(), "ai": "1"})


def go_l4(name):
    _nav({"sido": _sido(), "gu": _gu(), "dom": _dom(), "l4": name})


def clear_l4():
    _nav({"sido": _sido(), "gu": _gu(), "dom": _dom()})


def _chips(words, bg="#EAF4FB", fg="#2C5F6F"):
    return "".join(f"<span style='background:{bg};color:{fg};border-radius:10px;"
                   f"padding:2px 9px;font-size:12px;margin:2px 3px;display:inline-block'>{w}</span>"
                   for w in words)


def _crumb():
    sido, gu, dom, l4 = _sido(), _gu(), _dom(), _l4()
    # 클릭 가능한 breadcrumb(전국·시도는 링크) — 지도 위 '전국' 버튼 대체
    A = "color:#2C5F6F;text-decoration:none"
    cur = "color:#7B8794"
    parts = []
    if sido:
        parts.append(f"<a href='?' target='_self' style='{A}'>전국</a>")
    else:
        parts.append(f"<span style='{cur}'>전국</span>")
    if sido:
        if gu:
            parts.append(f"<a href='?sido={quote(sido)}' target='_self' style='{A}'>{sido}</a>")
        else:
            parts.append(f"<span style='{cur}'>{sido}</span>")
    if gu:
        if dom:
            parts.append(f"<a href='?sido={quote(sido)}&gu={quote(gu)}' target='_self' style='{A}'>{gu}</a>")
        else:
            parts.append(f"<span style='{cur}'>{gu}</span>")
    if dom:
        parts.append(f"<span style='{cur}'>{dom}</span>")
    if l4:
        parts.append(f"<span style='{cur}'>{l4}</span>")
    st.markdown(f"<div style='font-size:14px;line-height:1.9;padding-top:4px;"
                f"margin-bottom:2px'>📍 {' › '.join(parts)}</div>",
                unsafe_allow_html=True)


# ---- 분야 신호등 ----
# (2026-06-14) 구버전 _domain_cards 제거 — 미사용 dead code이자 domain_status 데모 예산/집행률(stale) 표시.
#  현행 = _domain_cards_g(실 districts_all l2 예산/집행 + 실 집행률 신호등).


def _render_bills_section(row):
    """입법② 지방의회 발의 의안 — 발의주체 중립 표기(자치입법 활성화 지수) + 면책.
    강남·인천 등 풀체인 자치구 공용."""
    if "의안수" not in row:
        return
    tot = row.get("의안수", 0)
    head_n, mem_n = row.get("의안단체장", 0), row.get("의안의원", 0)
    vit = round(mem_n / tot * 100) if tot else 0
    st.markdown(f"**입법 ② 지방의회 발의 의안** "
                f"<span style='color:#9AA5B1;font-size:12px'>(CLIK · 총 {tot}건)</span>",
                unsafe_allow_html=True)
    st.markdown(
        "<span style='font-size:13px;color:#52606D'>"
        f"🙋 의원 발의 <b>{mem_n}</b> · 🏛️ 집행부 제출 <b>{head_n}</b> · "
        f"📊 자치입법 활성화 지수 <b style='color:#2C5F6F'>{vit}%</b></span>",
        unsafe_allow_html=True)
    for bb in row.get("최근의안", []):
        dt = re.sub(r"\D", "", bb.get("발의일", "") or "")
        dt = f"{dt[:4]}.{dt[4:6]}.{dt[6:8]}" if len(dt) >= 8 else dt
        sbj = bb.get("발의주체", "")
        ic = "🏛️" if sbj == "단체장" else ("🙋" if sbj == "의원" else "📋")
        who = bb.get("대표발의") or sbj or "발의자 미상"
        st.markdown(f"- {ic} {bb['법안명']} <span style='color:#9AA5B1;font-size:12px'>"
                    f"({who} · {bb.get('종류','')} · {dt})</span>", unsafe_allow_html=True)
    st.caption("ⓘ '자치입법 활성화 지수' = 전체 의안 중 의원 발의 비율. 집행부 제출 의안도 "
               "의회 심의·의결을 거치며, 발의 건수는 입법 활동량 지표일 뿐 법안의 질·중요도와는 무관합니다.")


def _contracts(slug):
    p = POC / f"track_contracts_{slug}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d if d.get("계약수") else None


def _render_contract_crosscheck(sido, gu):
    """④집행 교차검증: 나라장터 실계약(업체·금액·계약방법) = 집행의 실체."""
    slug = _is_full_chain(sido, gu)
    c = _contracts(slug) if slug else None
    if not c:
        return
    from data_provenance import badge
    tot = c.get("계약총액", 0) / 1e8
    rows = "".join(
        f"<li>{x['계약명'][:34]} — <b>{x['계약금액']/1e8:.2f}억</b> "
        f"<span style='color:#9AA5B1'>{x['계약방법']}·{x['계약업체'][:12]}</span></li>"
        for x in c.get("계약", [])[:4])
    st.markdown(
        f"<div style='background:#FBF4F2;border-left:4px solid #C77A1B;border-radius:8px;"
        f"padding:9px 12px;margin:6px 0;font-size:12px'>"
        f"🔗 <b>집행 교차검증</b> {badge('A','A')} "
        f"<span style='color:#52606D'>예산서 통계(lofin365) ↔ 나라장터 실계약(조달청) 병치 — 돈이 실제 어디에 쓰였나</span><br>"
        f"<span style='color:#52606D'>📑 나라장터 계약 <b>{c['계약수']}</b>건 · 총 <b>{tot:.1f}억</b> · "
        f"수의계약 <b>{c.get('수의계약비율',0)}%</b> "
        f"<span style='color:#9AA5B1'>({c.get('기간','')} 표본)</span></span>"
        f"<ul style='margin:4px 0 0;padding-left:18px;color:#52606D'>{rows}</ul></div>",
        unsafe_allow_html=True)


def _render_budget_crosscheck(sido, gu, dom):
    """예산 교차검증: 편성 예산서(cpl) ↔ 집행 결산(ep) 2단계 생애주기 병치 + 이상치.
    2026-06-14: domain_status 데모 메타(stale) 대신 실 2025 결산(rec["l2"])으로 분야·전체 집행률·분야분류 재산출."""
    rec = district_rec(sido, gu)
    l2 = rec["l2"] if rec else []
    fld = next((x for x in l2 if x["분야"] == dom), None)
    if not fld or not l2:
        return
    from data_provenance import badge
    # 집행률 = 표시 금액 기준(예산_억/집행_억)으로 통일 → 메트릭·차트와 100% 일치(2026-06-14)
    def _ar(x):
        return (x.get("집행_억", 0) / x["예산_억"] * 100) if x.get("예산_억") else 0
    rt = _ar(fld)
    flag = ("✅ 편성대로 집행" if 80 <= rt <= 105 else
            ("⚠️ 미집행 점검" if rt < 80 else "🔺 추경·이월"))
    tot_b = sum(x.get("예산_억", 0) for x in l2)
    tot_e = sum(x.get("집행_억", 0) for x in l2)
    total_rate = (tot_e / tot_b * 100) if tot_b else 0
    n_ok = sum(1 for x in l2 if 80 <= _ar(x) <= 105)
    n_under = sum(1 for x in l2 if _ar(x) < 80)
    n_over = sum(1 for x in l2 if _ar(x) > 105)
    st.markdown(
        f"<div style='background:#F5F3F8;border-left:4px solid #7A5BA6;border-radius:8px;"
        f"padding:8px 12px;margin:4px 0;font-size:12px'>"
        f"🔗 <b>예산 교차검증</b> {badge('A','A')} "
        f"<span style='color:#52606D'>편성 예산서 ↔ 집행 결산 (lofin365 세출현황 — 사전 편성 vs 사후 결산 2단계 병치)</span><br>"
        f"<span style='color:#52606D'>이 분야 집행률 <b>{rt:.0f}%</b> {flag} · "
        f"자치구 전체 <b>{total_rate:.0f}%</b> "
        f"(정합 {n_ok} · 미집행 {n_under} · 추경·이월 {n_over} 분야)</span></div>",
        unsafe_allow_html=True)


def _render_ordinance_crosscheck(sido, gu):
    """조례 일치검증: CLIK 조례안(발의) ↔ law.go.kr 공포조례(법제처) 독립 2소스."""
    oc = (_dstat_meta(sido, gu) or {}).get("조례교차") or {}
    if not oc or not oc.get("발의조례안"):
        return
    from data_provenance import badge
    an, gong = oc["발의조례안"], oc["공포조례"]
    st.markdown(
        f"<div style='background:#F3F6F2;border-left:4px solid #3FA66A;border-radius:8px;"
        f"padding:8px 12px;margin:4px 0 8px;font-size:12px'>"
        f"🔗 <b>조례 교차검증</b> {badge('A','A')} "
        f"<span style='color:#52606D'>발의 조례안(CLIK·지방의정포털) ↔ 공포 조례(law.go.kr·법제처) — 독립 2소스 일치검증</span><br>"
        f"<span style='color:#52606D'>📝 발의 조례안 <b>{an:,}</b>건 → 📜 공포 조례 <b>{gong:,}</b>건 "
        f"<span style='color:#9AA5B1'>(발의→공포 입법사슬을 두 기관 데이터로 교차 확인. "
        f"발의 건수에는 개정·부결·계류 포함)</span></span></div>",
        unsafe_allow_html=True)


def _render_minutes_crosscheck(sido, gu):
    """의안↔회의록 교차검증(CLIK 2소스): 발의(bill) ↔ 심의(minutes) 보강."""
    mc = (_dstat_meta(sido, gu) or {}).get("회의록교차") or {}
    if not mc or not mc.get("회의록수"):
        return
    from data_provenance import badge
    st.markdown(
        f"<div style='background:#F2F7F8;border-left:4px solid #3B82A6;border-radius:8px;"
        f"padding:9px 12px;margin:6px 0;font-size:13px'>"
        f"🔗 <b>의안 교차검증</b> {badge('A','A')} "
        f"<span style='color:#52606D'>발의 의안(CLIK bill) ↔ 심의 회의록(CLIK minutes) 보강</span><br>"
        f"<span style='font-size:12px;color:#52606D'>"
        f"📋 회의록 <b>{mc['회의록수']:,}</b>건 표본 "
        f"(본회의 {mc.get('본회의수',0)} · 위원회 {mc.get('위원회수',0)}) · "
        f"🗓 {mc.get('회기수',0)}개 회기(최근 제{mc.get('최근회기','')}회) "
        f"— 발의된 의안이 실제 의회에서 심의된 활동을 독립 소스로 확인</span></div>",
        unsafe_allow_html=True)


def _domain_detail():
    dom = _dom()
    gbiz = district_l3().get("서울특별시|강남구", {}).get(dom, [])   # 재원 포함(L4/L5)
    if _l4():
        _l4_detail(gbiz, dom); return
    d = next((x for x in domain_status()["분야"] if x["분야"] == dom), None)
    st.button("◂ 분야 목록", on_click=clear_dom, key="back_dom")
    if not d:
        st.info("데이터 없음"); return
    # 예산 집행률·금액·신호등 = 아래 막대(차트)와 동일한 실 2025 세부사업 데이터(gbiz)로 산출.
    #  (기존엔 domain_status 데모 집행률/신호를 써 차트와 불일치 — 강남 일반공공행정 24% vs 실제 82%, 2026-06-14 해소)
    g_b = sum(r.get("예산_억", 0) for r in gbiz)
    g_e = sum(r.get("집행_억", 0) for r in gbiz)
    g_rate = (g_e / g_b * 100) if g_b else 0
    dot = SIG.get(_sig(g_rate), "#9AA5B1")
    st.markdown(f"#### <span style='color:{dot}'>●</span> {d['분야']} — 정책 이행 현황",
                unsafe_allow_html=True)
    k = st.columns(4)
    k[0].metric("관련 공약", f"{d['공약수']}건")
    k[1].metric("제정 조례", f"{d['조례수']}건")
    k[2].metric("발의 의안", f"{d['의안수']}건")
    k[3].metric("예산 집행률", f"{g_rate:.0f}%")
    st.caption(f"💰 총예산 {g_b:,.0f}억 · 실제 집행 {g_e:,.0f}억 (2025 결산 세부사업 합산, 아래 막대와 동일 기준)")

    st.markdown("**입법 ① 공포 조례** <span style='color:#9AA5B1;font-size:12px'>(law.go.kr 최종 조례)</span>",
                unsafe_allow_html=True)
    if d["최근조례"]:
        for o in d["최근조례"]:
            g = o["공포일자"]; g = f"{g[:4]}.{g[4:6]}.{g[6:8]}" if len(g) == 8 else g
            st.markdown(f"- 📜 {o['조례명']} <span style='color:#9AA5B1;font-size:12px'>"
                        f"({o['제개정']} {g})</span>", unsafe_allow_html=True)
    else:
        st.caption("이 분야 조례 미확인")

    _render_bills_section(d)

    st.markdown("**예산 → 집행 (세부사업)** <span style='color:#9AA5B1;font-size:12px'>막대 클릭 → 세부 상세</span>",
                unsafe_allow_html=True)
    _field_chart(gbiz, "세부사업", f"l3_gangnam_{d['분야']}", click=go_l4)

    st.markdown(
        f"<div style='background:#FFF7F3;border-left:4px solid #E8743B;border-radius:8px;"
        f"padding:12px 15px;margin-top:8px'>"
        f"🤖 <b>AI 갭 분석 — 남은 과제</b><br><span style='font-size:14px;color:#52606D'>"
        + "<br>".join(f"• {g}" for g in d["남은과제"]) +
        f"</span><br><br>🤖 <b>AI 제언</b><br><span style='font-size:14px;color:#52606D'>"
        + "<br>".join(f"→ {r}" for r in d["AI제언"]) + "</span></div>",
        unsafe_allow_html=True)


# ---- 레벨별 화면 ----
def _render_nation():
    # 대표 시연 쇼케이스 배너 — 4트랙 풀체인 자치구 바로가기(인천 예선 = 인천 우선 노출)
    st.markdown(
        "<div style='background:linear-gradient(90deg,#2C5F6F,#52606D);color:#fff;"
        "border-radius:10px;padding:12px 16px;margin-bottom:8px'>"
        "🏛️ <b>대표 시연 — 4트랙 풀체인</b> "
        "<span style='font-size:13px;opacity:.9'>공약→입법(조례·의안)→예산→집행을 한 자치구에서 끝까지</span></div>",
        unsafe_allow_html=True)
    # (2026-06-13) 첫 화면 상단 바로가기 버튼 2개 + 안내 캡션 제거 — 팀장 요청.
    # Windy식 상단 레이어 메뉴 — 지도 색 기준 전환(전부 실데이터)
    metric = st.segmented_control(
        "지도 색 기준", list(NATION_LAYERS.keys()), default="인구",
        key="nation_metric", label_visibility="collapsed") or "인구"
    st.markdown(mi.build(selected=None, stats=nation_stats(), fills=nation_fills(metric)),
                unsafe_allow_html=True)
    st.caption(f"🗺️ {NATION_LAYERS[metric]['cap']}. 마우스를 올리면 총예산·집행·인구, 클릭하면 이동합니다.")
    st.caption("ⓘ 총예산 = 전국 시·군·구(자치구) **순계 예산현액**(일반+특별회계, 기금·내부거래 제외 — 행안부 순계 방식). "
               "집행률 = 지출 ÷ 예산현액(결산 공식). 광역 시·도 본청 예산은 별도입니다. 출처: 지방재정365(**2025 회계연도 결산** · 2026 편성 예산 병기).")


def _render_vote(sat):
    """살기좋은 내 동네 만족도 투표(구 단위) + 회원가입 유도 CTA."""
    st.markdown("##### 🗳️ 우리 동네, 살기 좋나요? — 만족도 투표")
    st.caption("내 동네를 1~5점으로 평가하면 위 지도 색에 바로 반영됩니다. 시민이 함께 만드는 평가예요.")
    # 직전 투표 결과 안내(rerun 후 신선한 평균으로 표시)
    flash = st.session_state.pop("_vote_flash", None)
    if flash:
        cur = sat.get(flash[0], {})
        st.success(f"✅ {flash[0]}에 {flash[1]}점 투표 완료! 현재 평균 "
                   f"{cur.get('점수',0)}점 · 참여 {cur.get('참여',0):,}명")
    gus = list(sat.keys())
    with st.form("vote_form"):
        c1, c2, c3 = st.columns([2, 4, 1.2])
        gu = c1.selectbox("내 동네(구)", gus, index=gus.index("강남구") if "강남구" in gus else 0)
        score = c2.slider("살기좋은 만족도 (1~5점)", 1, 5, 4)
        submitted = c3.form_submit_button("투표하기")
    if submitted:
        cast_vote(gu, score)
        st.session_state["_vote_flash"] = (gu, score)
        st.rerun()
    # 회원가입/참여 유도
    st.markdown(
        "<div style='background:#FFF7F3;border-left:4px solid #E8743B;border-radius:8px;"
        "padding:13px 16px;margin-top:6px;font-size:14px;color:#52606D'>"
        "💡 <b>회원가입하면</b> 내 평가가 계속 반영되고, 동네 예산·공약 토론에도 참여할 수 있어요.<br>"
        "<b>茅亭 모정</b>은 시민이 모여 더 좋은 동네를 함께 만드는 플랫폼입니다."
        "</div>", unsafe_allow_html=True)
    st.button("회원가입하고 함께 만들기 (출시 예정)", disabled=True, key="signup_cta")


def _render_sido():
    sido = _sido()
    # L1 = 지도만 노출(상단 '전국' 버튼 제거 → breadcrumb 전국 링크로 대체)
    if sido == "서울특별시":
        # 참여형: 구 단위 '살기좋은 내 동네 만족도' 색(최대 #F98A77) + hover 도넛 + 투표
        sat = satisfaction()
        svg = mi.build_sigungu(sido, fills=seoul_sat_fills(),
                               stats=seoul_budget_stats(), sat=sat,
                               total=sido_total(sido))
        st.markdown(svg, unsafe_allow_html=True)
        st.caption("색이 진할수록 '살기좋은 내 동네' 만족도가 높은 구. 마우스를 올리면 "
                   "오른쪽에 위=예산·집행, 아래=만족도, 클릭하면 해당 구로 이동합니다.")
    elif sido == "경기도":
        # 경기 = 자치구 42개로 과밀 → 북부/남부 분할(좌=북·우=남 스와이프 토글)
        half = st.segmented_control("경기 권역", ["◀ 경기 북부", "경기 남부 ▶"],
                                    default="◀ 경기 북부", key="gg_half",
                                    label_visibility="collapsed")
        geo_key = "경기도(남)" if (half and "남부" in half) else "경기도(북)"
        svg = mi.build_sigungu(geo_key, selected=None, stats=sigungu_stats(sido),
                               total=sido_total(sido), href_sido="경기도")
        st.markdown(svg if svg else "지도 준비 중입니다.", unsafe_allow_html=True)
        st.caption("🗺️ 경기도 — 자치구 과밀로 북부/남부 분할 표시. 위 버튼으로 전환, "
                   "구·군 클릭 시 이동. (김포·고양·구리·남양주·양평 이북 = 북부)")
    else:
        # 그 외 시도: 예산·집행 도넛(기존)
        svg = mi.build_sigungu(sido, selected=None, stats=sigungu_stats(sido),
                               total=sido_total(sido))
        st.markdown(svg if svg else "이 시도의 시군구 지도는 준비 중입니다.",
                    unsafe_allow_html=True)
        st.caption(f"🗺️ {sido} — 구·군에 마우스를 올리면 예산·집행률, 클릭하면 이동. "
                   "(만족도 투표는 서울 시연 중 → 전국 확장 예정)")

    # ── 공통(전 시도, 지도 종류 무관): 특화 자산 테마 + AI 자동 발견 버튼 ──
    themes = _theme_list(sido)
    if themes:
        st.markdown(f"##### 🔥 {sido} 특화 자산 테마 "
                    "<span style='font-size:13px;color:#9AA5B1'>— 이 지역만의 현안을 예산·집행으로 교차 추적</span>",
                    unsafe_allow_html=True)
        cols = st.columns(len(themes) + 1)
        for i, (tk, lbl, sb, kw) in enumerate(themes):
            cols[i].button(lbl, on_click=go_theme, args=(tk,),
                           key=f"th_{sido}_{tk}", use_container_width=True, help=sb)
        if sido == "인천광역시":
            _asset_header(sido)        # 다른 시도와 동일한 자산 헤더 바
            _render_gateway_panel()    # + 인천 전용 공항·항만 gateway(정밀 실측 수치)
        else:
            _render_sido_asset_panel(sido)
    st.button(f"🤖 AI 자동 발견 — {sido} 자치구 시민감시 지표(수의계약·집행률 등) ▸",
              on_click=go_ai, key=f"ai_{sido}", use_container_width=True)
    _render_elected(sido)             # 광역단체장(시장·도지사) 2026 당선자 + 공약
    # 서울: 만족도 투표 = AI/테마 버튼 아래로(2026-06-09)
    if sido == "서울특별시":
        _render_vote(satisfaction())


# ── 시도별 특화 자산 개요(인천 gateway의 일반 시도판) — 서술형 대표 자산 + 대표 테마 실예산 ──
SIDO_ASSET = {
    "서울특별시": ("🏙️ 수도·경제 심장", "금융·IT 본사 집적 — 청년 주거·도시 노후안전이 핵심 현안"),
    "인천광역시": ("✈️🚢 관문 경제", "인천국제공항·인천항 — 공항·항만 양대 국가관문을 동시에 가진 유일 도시"),
    "부산광역시": ("🚢 제1의 무역항", "부산항 컨테이너 물동량 전국 1위 — 해양·수산·영상의 도시"),
    "대구광역시": ("🏥 의료·미래모빌리티", "첨단의료복합단지(메디시티) — 로봇·자동차부품 산업"),
    "광주광역시": ("🤖 AI·문화", "국가 AI 집적단지 — 광(光)산업·아시아문화중심도시"),
    "대전광역시": ("🔬 과학수도", "대덕연구개발특구(정부출연연 집적) — 바이오·국방 R&D"),
    "울산광역시": ("🏭 산업수도", "자동차·조선·석유화학 3대 주력 — 친환경 수소 전환 선도"),
    "경기도": ("🔌 최대 광역", "인구 전국 1위 — 반도체 클러스터(삼성·SK)·광역교통(GTX)"),
    "강원도": ("⛰️ 관광·산림", "설악·동해·DMZ — 폐광지역 에너지 전환, 청정 자연"),
    "충청북도": ("🧬 바이오·소재", "오송 첨단의료복합단지 — 2차전지·반도체 소재"),
    "충청남도": ("🏭 산업·농업", "서산 석유화학·아산 디스플레이 — 서해안 해양·농축산"),
    "전라북도": ("🌾 농생명·새만금", "국가식품클러스터 — 새만금 재생에너지·신산업"),
    "전라남도": ("🌊 신재생 메카", "신안 해상풍력(아태 최대 추진)·태양광 — 다도해·농수산 1위"),
    "경상북도": ("🏭 철강·원자력", "포항 철강(포스코) — 원전 밀집·문화유산의 보고"),
    "경상남도": ("🚀 조선·항공우주", "거제 조선 빅3 — 사천 항공우주(KAI)·기계·방산"),
    "세종특별자치시": ("🏛️ 행정수도", "행정중심복합도시 — 정부세종청사·스마트시티 실증"),
    "제주특별자치도": ("🌴 관광·청정", "국제관광도시 — 풍력·태양광 청정에너지, 감귤·수산"),
}


def _asset_header(sido):
    """시도 특화 자산 헤더 바(파란 띠) — 전 시도 공통(인천 포함)."""
    a = SIDO_ASSET.get(sido)
    if not a:
        return
    title, desc = a
    st.markdown(
        f"<div style='background:linear-gradient(90deg,#1F4654,#2C5F6F);color:#fff;"
        f"border-radius:10px;padding:10px 16px;margin:8px 0 2px'>"
        f"<b style='font-size:15px'>{title}</b> "
        f"<span style='opacity:.85;font-size:12.5px'>— 다른 지자체엔 없는 {sido} 고유 자산 · {desc}</span></div>",
        unsafe_allow_html=True)


def _render_sido_asset_panel(sido):
    """시도 특화 자산 — 대표 자산 헤더 + 테마별 실예산 '큰 숫자' 메트릭 카드(인천 gateway 스타일·전 시도)."""
    themes = _theme_list(sido)
    if not themes:
        return
    _asset_header(sido)
    cols = st.columns(len(themes))
    for i, (tk, lbl, sb, kw) in enumerate(themes):
        recs = _theme_recs(sido, kw)
        tot = sum(r["예산_억"] for r in recs)
        n = sum(r["사업수"] for r in recs)
        cols[i].metric(lbl, f"{tot:,.0f}억", f"세부사업 {n}건", delta_color="off")
    st.caption("📊 특화 자산별 관련 예산(지방재정365 세부사업 실집계). 위 🔥테마 버튼으로 자치구별 상세 비교 가능.")


@st.cache_data
def _incheon_gateway():
    p = POC / "incheon_gateway.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _render_gateway_panel():
    """인천 특화 '관문 경제' — 공항(인천국제공항공사)·항만(인천항만공사) 지표.
    다른 지자체에 없는 인천 고유 국가관문 자산을 지역경제·환경 사슬에 연결."""
    g = _incheon_gateway()
    if not g:
        return
    st.markdown(
        "##### ✈️🚢 인천 관문 경제 "
        "<span style='font-size:13px;color:#9AA5B1'>— 공항·항만(인천 관내 공공기관) · 다른 지자체엔 없는 인천 고유 자산</span>",
        unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    air, port = g["공항"], g["항만"]
    with c1:
        st.markdown(f"**🛫 인천국제공항** <span style='color:#9AA5B1;font-size:11px'>{air['기관']}</span>",
                    unsafe_allow_html=True)
        m = st.columns(2)
        m[0].metric("2024 여객", f"{air['여객_2024_만명']:,}만명", help=air["비고"])
        m[1].metric("2024 화물", f"{air['화물_2024_만톤']:,}만톤")
        st.caption(f"국제선 여객 역대 최다 · 세계 3위. 출처: {air['출처']}.")
    with c2:
        st.markdown(f"**🚢 인천항** <span style='color:#9AA5B1;font-size:11px'>{port['기관']}</span>",
                    unsafe_allow_html=True)
        m = st.columns(2)
        m[0].metric("2024 컨테이너", f"{port['컨_2024_만TEU']:.1f}만TEU", help=port["비고"])
        tr = port["추세"]
        delta = tr[-1]["TEU_만"] - tr[-2]["TEU_만"]
        m[1].metric("전년 대비", f"+{delta:.1f}만TEU", help="역대 최대 경신")
        trend = " → ".join(f"{x['연도']} {x['TEU_만']:.0f}만" for x in tr)
        st.caption(f"물동량 추이: {trend}(TEU). 출처: {port['출처']}.")
    st.caption(f"💡 {g['설명']}")


def _emd_image(sido, gu):
    """행정동(읍면동) 경계 = 클릭 불가 지도 이미지(전국 자치구)."""
    disp = _parent_gu(gu)
    img = mi.build_emd_image(sido, gu) or mi.build_emd_image(sido, disp)
    st.markdown(f"**🗺️ {disp} 행정동 경계** "
                "<span style='color:#9AA5B1;font-size:12px'>(클릭 불가 · 참고 이미지)</span>",
                unsafe_allow_html=True)
    if img:
        st.markdown(img, unsafe_allow_html=True)
    else:
        st.caption("이 자치구의 행정동 경계 이미지는 준비 중입니다.")
    st.caption("ⓘ 동·읍은 독립 예산·조례가 없어(재정·입법 최소단위=자치구) 경계 표시·정보용입니다.")


@st.cache_resource   # 13MB 최대 로더 → 단일객체 공유(세션마다 13MB 복사 제거 = 로딩 가속 핵심)
def district_l3():
    """전국 자치구별 L3 세부사업(build_district_l3.py). {"시도|자치구": {분야:[세부사업..]}}."""
    p = POC / "district_l3.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@st.cache_resource   # 순수 JSON 로더 → 공유
def budget_2026():
    """2026 예산안(편성·예산현액) — districts_2026.json {시도:{구:{l1,l2}}}. 없으면 {}.
    (주 데이터=2025 결산. 2026은 회계연도 진행 중이라 '편성 계획'으로 병기, 집행률은 연말 확정.)"""
    p = POC / "districts_2026.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _b2026_field(sido, gu, dom):
    """2026 해당 분야 편성 예산(억). 없으면 None."""
    rec = budget_2026().get(sido, {}).get(_parent_gu(gu))
    if not rec:
        return None
    f = next((x for x in rec.get("l2", []) if x["분야"] == dom), None)
    return f["예산_억"] if f else None


def _b2026_total(sido, gu):
    """2026 자치구 편성 총예산(억). 없으면 None."""
    rec = budget_2026().get(sido, {}).get(_parent_gu(gu))
    return rec["l1"]["총예산_억"] if rec else None


@st.cache_resource   # 순수 JSON 로더 → 공유
def budget_2024():
    """2024 회계연도 결산 — districts_2024.json {시도:{구:{l1,l2}}}. 없으면 {}. (3개년 추세용)"""
    p = POC / "districts_2024.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _b2024_field(sido, gu, dom):
    """2024 해당 분야 결산 {예산_억, 집행률}. 없으면 None."""
    rec = budget_2024().get(sido, {}).get(_parent_gu(gu))
    if not rec:
        return None
    f = next((x for x in rec.get("l2", []) if x["분야"] == dom), None)
    return ({"예산_억": f["예산_억"], "집행률": f["집행률"]}) if f else None


def _b2024_total(sido, gu):
    """2024 자치구 결산 총예산(억). 없으면 None."""
    rec = budget_2024().get(sido, {}).get(_parent_gu(gu))
    return rec["l1"]["총예산_억"] if rec else None


@st.cache_resource   # 1.9MB 순수 JSON 로더 → 공유
def domain_status_all():
    """전국 자치구 분야별 이행(조례+AI, build_domain_status_nationwide.py)."""
    p = POC / "domain_status_all.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# 4트랙 풀체인(공약·조례·의안·예산·집행) 완비 자치구 → domain_status_{slug}.json
FULL_CHAIN = {
    "서울특별시|강남구": "gangnam",
    "인천광역시|중구": "incheon_jung",
    "인천광역시|동구": "incheon_dong",
    "인천광역시|미추홀구": "incheon_michuhol",
    "인천광역시|연수구": "incheon_yeonsu",
    "인천광역시|남동구": "incheon_namdong",
    "인천광역시|부평구": "incheon_bupyeong",
    "인천광역시|계양구": "incheon_gyeyang",
    "인천광역시|서구": "incheon_seo",
    "인천광역시|강화군": "incheon_ganghwa",
    "인천광역시|옹진군": "incheon_ongjin",
}


@st.cache_resource   # 순수 JSON 로더(slug별 공유)
def _domain_status_slug(slug):
    p = POC / f"domain_status_{slug}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"분야": []}


def _is_full_chain(sido, gu):
    return FULL_CHAIN.get(f"{sido}|{_parent_gu(gu)}")


def _dstat(sido, gu):
    """자치구 분야별 이행 {분야: row}. 풀체인 자치구는 의안·발의주체 포함 정본 사용."""
    slug = _is_full_chain(sido, gu)
    if slug:
        d = _domain_status_slug(slug)
    else:
        d = domain_status_all().get(f"{sido}|{_parent_gu(gu)}", {})
    return {x["분야"]: x for x in d.get("분야", [])}


def _dstat_meta(sido, gu):
    """풀체인 자치구 domain_status 메타(회의록교차 등)."""
    slug = _is_full_chain(sido, gu)
    return _domain_status_slug(slug).get("메타", {}) if slug else {}


def _sig(rate):
    return "green" if rate >= 80 else ("amber" if rate >= 50 else "red")


_GRADE_COLOR = {"A": "#3FA66A", "B": "#3B82A6", "C": "#E8A13B", "D": "#9AA5B1"}


def _transparency_grade(sido, gu):
    """자치구 데이터 공개 완성도 등급(A~D) — 채워진 4트랙 수 기준."""
    full = _is_full_chain(sido, gu)
    ds = _dstat(sido, gu)
    has_ord = any(r.get("조례수") for r in ds.values())
    has_bill = any(r.get("의안수") for r in ds.values())
    has_bud = bool(district_rec(sido, gu))
    if full and has_bill and has_ord:
        return "A", "공약·조례·의안·예산·집행 전 트랙 연결"
    if has_ord and has_bud:
        return "B", "조례·예산·집행 연결(의안·공약 확장 예정)"
    if has_bud:
        return "C", "예산·집행 공개(조례·의안 미연계)"
    return "D", "데이터 부분 공개"


def _field_chart(rows, name_key, base_key, click=None, topn=14):
    """가로 막대 + 우상단 스와이프 버튼(집행률 ↔ 규모 비교, 애니메이션). 고정(드래그/줌 차단).
    · 집행률 뷰: 100% 정규화(파란 길이=집행률) → 비교 쉬움.
    · 규모 비교 뷰: 예산을 sqrt 압축(최대=최장)으로 '이만큼 차이' 표시(절대값 X, 작은 것도 보임).
    click 주면 바 클릭→드릴. L2/L3 공용."""
    import plotly.graph_objects as go
    data = [r for r in rows
            if (r.get("예산_억", 0) or r.get("집행_억", 0)) and r.get(name_key) != "예비비"]
    data = sorted(data, key=lambda r: -r.get("예산_억", 0))[:topn][::-1]   # 큰 예산이 위로
    if not data:
        st.caption("데이터 없음"); return
    names = [r[name_key] for r in data]
    disp = [(n[:16] + "…") if len(n) > 17 else n for n in names]
    ypos = list(range(len(data)))
    bud = [r.get("예산_억", 0) for r in data]
    exe = [r.get("집행_억", 0) for r in data]
    rate = [min(round(e / b * 100), 100) if b else 100 for b, e in zip(bud, exe)]
    cdata = [[n, b, e, r] for n, b, e, r in zip(names, bud, exe, rate)]
    htmpl = ("%{customdata[0]}<br>예산 %{customdata[1]:,.0f}억 · 집행 %{customdata[2]:,.0f}억 · "
             "집행률 %{customdata[3]:.0f}%<extra></extra>")

    # 스와이프 토글(입체 스위치): 시작(OFF/왼쪽)=예산 규모 비교 / ON(오른쪽)=집행률
    tc = st.columns([5, 1.5])
    with tc[1]:
        rate_view = st.toggle("집행률 보기", key=f"rmode_{base_key}",
                              help="왼쪽=예산 규모 비교 · 오른쪽=집행률")
    if not rate_view:
        bmax = max(bud) or 1
        sc = [(math.sqrt(b) / math.sqrt(bmax) * 100) if b > 0 else 0 for b in bud]
        x1 = [round(s * r / 100, 2) for s, r in zip(sc, rate)]
        x2 = [round(s - e, 2) for s, e in zip(sc, x1)]
        xsuf, xtick = "", False
    else:
        x1, x2 = rate, [100 - x for x in rate]
        xsuf, xtick = "%", True

    fig = go.Figure()
    # 안정 uid → 토글 시 Plotly.react 가 막대를 부드럽게 트윈(아래 transition)
    fig.add_bar(y=ypos, x=x1, orientation="h", name="집행", marker_color="#2C7FB8",
                customdata=cdata, hovertemplate=htmpl, uid="ex")
    fig.add_bar(y=ypos, x=x2, orientation="h", name="미집행", marker_color="#DCE3E9",
                customdata=cdata, hovertemplate=htmpl, uid="rem")
    for yp, b in zip(ypos, bud):
        fig.add_annotation(x=100, y=yp, text=f"{b:,.0f}억", showarrow=False,
                           xanchor="left", xshift=6, font=dict(size=11, color="#52606D"))
    fig.update_layout(barmode="stack", height=max(len(data) * 38, 210),
        margin=dict(l=0, r=132, t=8, b=10), bargap=0.32, plot_bgcolor="white", dragmode=False,
        transition=dict(duration=600, easing="cubic-in-out"),
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, font=dict(size=11)),
        xaxis=dict(range=[0, 100], ticksuffix=xsuf, showticklabels=xtick, showgrid=True,
                   gridcolor="#F1F4F7", zeroline=False, fixedrange=True),
        yaxis=dict(tickmode="array", tickvals=ypos, ticktext=disp, automargin=True, fixedrange=True))
    cfg = {"displayModeBar": False, "scrollZoom": False}

    if click:
        key = f"{base_key}_{st.session_state.get('chartnav', 0)}"
        ev = st.plotly_chart(fig, key=key, on_select="rerun",
                             use_container_width=True, config=cfg)
        pts = ev.get("selection", {}).get("points") if ev else None
        if pts:
            lbl = pts[0].get("customdata")
            if isinstance(lbl, list):
                lbl = lbl[0] if lbl else None
            if lbl:
                st.session_state["chartnav"] = st.session_state.get("chartnav", 0) + 1
                click(lbl); st.rerun()
        st.caption("막대를 클릭하면 세부 단계로 이동합니다. 시작 화면=예산 규모 비교 · 우측 스위치를 켜면 집행률")
    else:
        st.plotly_chart(fig, key=base_key, use_container_width=True, config=cfg)


# ===== 환경·교통 킬러 테마 뷰 (인천 지역현안 특화) =====
_THEME_MAP = {
    "env":       ("환경",      "🌍", "탄소중립·미세먼지·폐기물·공원"),
    "transport": ("교통및물류", "🚌", "출퇴근·도로·대중교통·주차"),
}
_INCHEON_GU = [(k.split("|")[1], v) for k, v in FULL_CHAIN.items() if k.startswith("인천")]

# ── 시도별 특화 자산 테마 카탈로그 (2026-06-09) ──────────────────────────────
# 각 시도의 복제불가 특화 현안을 세부사업명 키워드로 추출 → 테마뷰.
# 구조: 시도키 → [(테마키, 라벨(이모지포함), 부제, 키워드정규식), ...]
# ※인천 env/transport는 기존 리치뷰(의안·계약·성과 패널) 유지 → _INCHEON_RICH 로 분기.
THEME_CATALOG = {
    "서울특별시": [
        ("safety", "🏙️ 도시안전", "노후·재난·침수·방재", "노후|재난|침수|방재|내진|안전관리|화재|cctv|범죄예방|보행안전|싱크홀|침하"),
        ("youth", "🧑 청년·주거", "청년·주거·일자리", "청년|주거|임대주택|역세권|일자리|창업지원|신혼|전월세"),
    ],
    "부산광역시": [
        ("port", "🚢 항만·물류", "항만·물류·해운", "항만|물류|부두|해운|컨테이너|북항|신항|배후단지"),
        ("marine", "🌊 해양·수산", "해양·수산·어촌", "해양|수산|어항|어촌|연안|갯벌|어업|선박"),
        ("tour", "🎬 관광·문화", "관광·영화·축제", "관광|영화|영상|축제|컨벤션|해수욕장|문화관광|마이스"),
    ],
    "대구광역시": [
        ("medical", "🏥 의료·바이오", "의료·헬스·바이오", "의료|병원|헬스|바이오|의약|첨단의료|메디|건강"),
        ("mobility", "🚗 미래모빌리티", "자동차·로봇·부품", "자동차|모빌리티|로봇|부품|전기차|미래차|기계"),
    ],
    "인천광역시": [
        ("env", "🌍 환경", "탄소중립·미세먼지·폐기물", "환경|탄소|미세먼지|대기|폐기물|재활용|하수|상수|공원|녹지"),
        ("transport", "🚌 교통·물류", "출퇴근·도로·대중교통", "교통|도로|버스|지하철|철도|주차|보행|물류"),
        ("gateway", "✈️ 공항·항만", "관문경제·공항·항만", "공항|항만|항공|크루즈|물류|배후단지"),
    ],
    "광주광역시": [
        ("ai", "🤖 AI·미래산업", "AI·광산업·창업혁신", "인공지능|에이아이|디지털|소프트웨어|정보화|스마트|빅데이터|메타버스|ict|광산업|광융합|창업|벤처|혁신|미래산업|데이터"),
        ("culture", "🎨 문화·예술", "문화·예술·관광", "문화|예술|미술|디자인|콘텐츠|관광|축제"),
    ],
    "대전광역시": [
        ("science", "🔬 과학·R&D", "과학·연구·기술", "과학|연구|기술개발|대덕|벤처|혁신|실증|특구"),
        ("biotech", "🧬 바이오·헬스", "바이오·의료", "바이오|의료|병원|제약|헬스|건강"),
    ],
    "울산광역시": [
        ("industry", "🏭 주력산업", "자동차·조선·화학", "자동차|조선|석유화학|화학|기계|중공업|부품"),
        ("energy", "⚡ 친환경에너지", "수소·에너지전환", "수소|에너지|신재생|태양광|풍력|연료전지|친환경"),
    ],
    "경기도": [
        ("semicon", "🔌 산업·반도체", "반도체·제조·산단", "반도체|산업단지|제조|첨단산업|소부장|클러스터|기업유치"),
        ("transport", "🚄 광역교통", "GTX·도로·대중교통", "교통|도로|철도|버스|광역|지하철|주차|환승"),
        ("housing", "🏘️ 주거·도시", "주거·도시개발", "주거|신도시|임대주택|도시개발|재개발|택지|정비사업"),
    ],
    "강원도": [
        ("tour", "⛰️ 관광·산림", "관광·산림·휴양", "관광|산림|숲|휴양|레저|축제|국립공원|치유|둘레길"),
        ("energy", "⚡ 에너지·자원", "폐광·수소·에너지", "폐광|석탄|수소|에너지|신재생|풍력|발전|광산"),
    ],
    "충청북도": [
        ("biohealth", "🧬 바이오·헬스", "바이오·의약·화장품", "바이오|의약|제약|화장품|헬스|오송|의료"),
        ("battery", "🔋 2차전지·반도체", "이차전지·반도체·소재", "전지|배터리|반도체|이차전지|소재|첨단"),
    ],
    "충청남도": [
        ("industry", "🏭 산업·디스플레이", "석유화학·디스플레이", "석유화학|디스플레이|철강|산업단지|제조|반도체"),
        ("agri", "🌾 농업·축산", "농업·축산·농촌", "농업|농촌|농가|축산|친환경농|농산물|쌀"),
        ("coast", "🌊 서해안·해양", "해양·수산·연안", "해양|수산|어항|연안|갯벌|어촌|항만"),
    ],
    "전라북도": [
        ("agbio", "🌾 농생명", "농업·식품·바이오", "농업|농촌|농생명|식품|종자|농산물|축산|친환경농"),
        ("saemangeum", "🏗️ 새만금·신산업", "새만금·재생에너지", "새만금|재생에너지|태양광|풍력|수소|신산업|이차전지"),
    ],
    "전라남도": [
        ("energy", "🌊 신재생에너지", "태양광·해상풍력·수소", "태양광|풍력|신재생|재생에너지|수소|연료전지|에너지자립|전기차충전"),
        ("island", "🏝️ 섬·해양", "다도해·어항·연안", "섬|도서|어항|항만|연안|해양|갯벌|어촌|여객선|선착장|방파제"),
        ("agri", "🌾 농수산", "농업·수산·김", "농업|농촌|농가|수산|축산|임업|김|친환경농|농산물|어업"),
    ],
    "경상북도": [
        ("steel", "🏭 철강·소재", "철강·소재·전자", "철강|소재|전자|부품|산업단지|제조|배터리"),
        ("energy", "⚛️ 원자력·에너지", "원자력·수소·에너지", "원자력|원전|수소|에너지|신재생|발전|풍력"),
        ("heritage", "🏛️ 농업·문화유산", "농업·문화재·관광", "농업|농촌|문화재|유산|관광|전통|한옥"),
    ],
    "경상남도": [
        ("aero", "🚀 항공우주·방산", "항공·우주·방위산업", "항공|우주|방위|방산|항공기|드론|위성"),
        ("ship", "🚢 조선·해양", "조선·해양·기계", "조선|해양|수산|어항|기계|중공업|선박|항만"),
    ],
    # 세종·제주 = 단일 광역(자치구 없음). 단일 뷰. 2025 결산·순계(타 시도와 동일 기준, 2026-06-10 V2 통일).
    "세종특별자치시": [
        ("city", "🏙️ 자족·도시", "행정중심·자족·도시개발", "자족|도시개발|행정|신도시|중앙행정|정주여건|일자리|산업단지|정주"),
        ("smart", "🌳 스마트·환경", "스마트시티·친환경·교통", "스마트|친환경에너지|환경|교통|brt|간선급행|자율주행|디지털|탄소중립|녹지"),
    ],
    "제주특별자치도": [
        ("tour", "🌴 관광", "관광·MICE·문화", "관광|마이스|컨벤션|문화|축제|올레|레저|숙박|크루즈"),
        ("energy", "⚡ 청정에너지", "풍력·태양광·전기차", "풍력|태양광|신재생|재생에너지|수소|전기차|충전|에너지신산업|지역에너지|에너지정책|탄소중립|그린수소"),
        ("primary", "🍊 1차산업", "감귤·수산·축산", "감귤|밭작물|농산물|수산|축산|어업|양식|어항|임업"),
    ],
}
_INCHEON_RICH = {"env", "transport"}  # 인천 이 두 테마만 기존 리치뷰(성과 패널 포함) 유지


def _theme_list(sido):
    return THEME_CATALOG.get(sido, [])


def _theme_def(sido, theme):
    for t in _theme_list(sido):
        if t[0] == theme:
            return t
    return None


@st.cache_data
def _theme_recs(sido, kw):
    """시도 자치구별로 세부사업명이 kw정규식에 매칭되는 예산·집행 집계."""
    pat = re.compile(kw, re.I)
    l3 = district_l3()
    recs = []
    _jeju_done = False
    for key, doms in l3.items():
        if not key.startswith(sido + "|"):
            continue
        gu = key.split("|", 1)[1]
        # 제주: 제주시·서귀포시가 道 통합 재정으로 동일 → 1회만 집계(이중계산 방지)
        if sido == "제주특별자치도":
            if _jeju_done:
                continue
            _jeju_done = True
            gu = "제주도(통합)"
        bud = exe = 0.0
        n = 0
        for rows in doms.values():
            for r in rows:
                if pat.search(r.get("세부사업", "") or ""):
                    bud += r.get("예산_억", 0) or 0
                    exe += r.get("집행_억", 0) or 0
                    n += 1
        if bud > 0:
            recs.append({"구": gu, "예산_억": bud, "집행_억": exe,
                         "집행률": (exe / bud * 100 if bud else 0), "사업수": n})
    recs.sort(key=lambda x: -x["예산_억"])
    return recs


@st.cache_data
def _theme_projects(sido, kw):
    """테마 키워드 매칭 '세부사업' 단위 집계(동일 세부사업명 합산). 단일 광역(제주·세종)에서
    시군구 막대 1개 대신 세부사업 breakdown(세부 화면)을 바로 보여주기 위함."""
    pat = re.compile(kw, re.I)
    l3 = district_l3()
    agg = {}
    _jeju_done = False
    for key, doms in l3.items():
        if not key.startswith(sido + "|"):
            continue
        if sido == "제주특별자치도":          # 제주 = 道 통합 재정 동일 → 1회만(이중계산 방지)
            if _jeju_done:
                continue
            _jeju_done = True
        for rows in doms.values():
            for r in rows:
                nm = r.get("세부사업", "") or ""
                if nm and pat.search(nm):
                    a = agg.setdefault(nm, {"세부사업": nm, "예산_억": 0.0, "집행_억": 0.0})
                    a["예산_억"] += r.get("예산_억", 0) or 0
                    a["집행_억"] += r.get("집행_억", 0) or 0
    out = []
    for a in agg.values():
        a["집행률"] = (a["집행_억"] / a["예산_억"] * 100) if a["예산_억"] else 0
        out.append(a)
    out.sort(key=lambda x: -x["예산_억"])
    return out


def _open_gu_generic(gu):
    if gu == "제주도(통합)":   # 제주 단일 막대 → 제주시 상세로
        gu = "제주시"
    _nav({"sido": _sido(), "gu": gu})


def _render_theme_generic(sido, theme):
    """시도 특화자산 테마뷰(키워드 기반) — 세부사업 예산·집행 교차집계 + 이상치 경고등."""
    tdef = _theme_def(sido, theme)
    if not tdef:
        clear_theme(); st.rerun(); return
    _, label, sub, kw = tdef
    icon = label.split()[0] if label.split() else "📌"
    name = label.split(" ", 1)[1] if " " in label else label

    st.button(f"◂ {sido} 지도", on_click=go_sido, args=(sido,), key="theme_back_g")
    themes = _theme_list(sido)
    if len(themes) > 1:
        opts = [t[1] for t in themes]
        cur = label
        pick = st.segmented_control("테마", opts, default=cur, key="theme_pick_g",
                                    label_visibility="collapsed")
        if pick and pick != cur:
            go_theme(themes[opts.index(pick)][0]); st.rerun()

    st.markdown(
        f"### {icon} {sido} {name} 특화 투명성 "
        f"<span style='font-size:14px;color:#9AA5B1'>— {sub}</span>", unsafe_allow_html=True)
    st.caption(f"{sido} 자치구의 '{name}' 관련 세부사업을 예산→집행으로 교차 추적. "
               f"키워드: {kw.replace('|', ', ')}")

    recs = _theme_recs(sido, kw)
    if not recs:
        st.info("이 테마에 해당하는 세부사업 데이터가 없습니다."); return

    tot_b = sum(r["예산_억"] for r in recs)
    tot_e = sum(r["집행_억"] for r in recs)
    rate = tot_e / tot_b * 100 if tot_b else 0
    n_proj = sum(r["사업수"] for r in recs)
    anomaly = [r for r in recs if r["집행률"] < 80 or r["집행률"] > 105]

    k = st.columns(4)
    k[0].metric("테마 예산" if len(recs) == 1 else f"테마 예산({len(recs)}개 시군구)", f"{tot_b:,.0f}억")
    k[1].metric("평균 집행률", f"{rate:.0f}%")
    k[2].metric("매칭 세부사업", f"{n_proj:,}건")
    k[3].metric("⚠️ 집행 이상치", f"{len(anomaly)}곳")

    if len(recs) == 1:
        # 단일 광역(제주·세종 등) = 시군구 비교 막대가 1개뿐 → 막대 화면 대신 '세부사업' breakdown(세부 화면)을
        #  바로 표시(2026-06-11). 세부사업은 이미 최하위 상세 → 클릭 비활성(2026-06-12): 개별 사업 집행률(예: 5%)을
        #  클릭하면 지역 전체(예: 93%)로 점프해 '레벨이 다른 두 수치'가 모순처럼 보이던 혼란 제거.
        projs = _theme_projects(sido, kw)
        st.markdown(f"**📋 {recs[0]['구'].replace('(통합)','')} 세부사업별 예산·집행** "
                    "<span style='color:#9AA5B1;font-size:12px'>(이 지역의 세부사업 단위 집행 현황)</span>",
                    unsafe_allow_html=True)
        if projs:
            _field_chart(projs, "세부사업", f"thgp_{sido}_{theme}", topn=15)   # click 없음 = 정적(점프 제거)
        else:
            _field_chart(recs, "구", f"thg_{sido}_{theme}", click=_open_gu_generic, topn=15)
    else:
        st.markdown("**🏙️ 시군구별 테마 예산·집행 비교** "
                    "<span style='color:#9AA5B1;font-size:12px'>(막대 클릭 → 해당 시군구 상세)</span>",
                    unsafe_allow_html=True)
        _field_chart(recs, "구", f"thg_{sido}_{theme}", click=_open_gu_generic, topn=15)

    if anomaly:
        items = "".join(
            f"<li><b>{r['구']}</b> — 집행률 <b>{r['집행률']:.0f}%</b> "
            + ("⚠️ 미집행(80% 미만 — 착공지연·보상 등 점검)" if r['집행률'] < 80
               else "🔺 추경·이월(편성 초과)") + "</li>"
            for r in sorted(anomaly, key=lambda x: x['집행률'])[:12])
        st.markdown(
            f"<div style='background:#FCF3F0;border-left:4px solid #C0552B;border-radius:8px;"
            f"padding:9px 13px;margin:6px 0;font-size:13px'>"
            f"🚨 <b>테마 예산 집행 이상치 경고등</b> "
            f"<span style='color:#9AA5B1;font-size:12px'>(편성↔결산 · 정상 80~105% 밖)</span>"
            f"<ul style='margin:5px 0 0;padding-left:20px;color:#52606D'>{items}</ul></div>",
            unsafe_allow_html=True)
    else:
        st.caption("✅ 해당 시군구 모두 정상 집행 범위(80~105%) 내")
    st.caption("※ 세부사업명 키워드 매칭 집계(지방재정365 세출현황). "
               "특화 자산 = 해당 시도의 복제불가 현안. 의안·계약 교차는 자치구 상세에서 확인.")
    if sido in ("세종특별자치시", "제주특별자치도"):
        st.caption("※ 세종·제주는 단일 광역(자치구 없음)이라 시·도 본청 재정으로 표기 "
                   "(**2025 결산·순계**, 타 시도와 동일 기준). 제주는 행정시(제주시·서귀포시)가 "
                   "자치권이 없어 道 통합 재정을 동일 적용.")


def _open_gu_theme(gu):
    """테마 비교 막대 클릭 → 해당 구의 그 분야(환경/교통) 상세로 이동."""
    fld = _THEME_MAP.get(_theme(), ("환경",))[0]
    _nav({"sido": _sido(), "gu": gu, "dom": fld})


def _theme_contract_count(slug, fld):
    """구 나라장터 계약 중 분야(환경/교통) 키워드 매칭 건수·금액. None=계약 데이터 미수집."""
    c = _contracts(slug)
    if not c:
        return None
    try:
        from build_domain_status import classify
    except Exception:
        return {"수": 0, "금액": 0.0}
    sub = [x for x in c.get("계약", []) if classify(x.get("계약명", "")) == fld]
    return {"수": len(sub), "금액": sum(x["계약금액"] for x in sub) / 1e8}


def _render_theme_detail():
    """테마뷰 라우터: 인천 env/transport=리치뷰(성과 패널) / 그 외 전 시도=일반화 키워드 뷰."""
    sido, theme = _sido(), _theme()
    if not (sido == "인천광역시" and theme in _INCHEON_RICH):
        if _theme_def(sido, theme):
            _render_theme_generic(sido, theme); return
        clear_theme(); st.rerun(); return
    fld, icon, sub = _THEME_MAP[theme]
    disp = "환경" if theme == "env" else "교통"

    st.button("◂ 인천 지도", on_click=go_sido, args=(sido,), key="theme_back")
    pick = st.segmented_control("테마", ["🌍 환경", "🚌 교통"],
                                default=("🌍 환경" if theme == "env" else "🚌 교통"),
                                key="theme_pick", label_visibility="collapsed")
    if pick and (("환경" in pick) != (theme == "env")):
        go_theme("env" if "환경" in pick else "transport"); st.rerun()

    st.markdown(
        f"### {icon} 인천 {disp} 투명성 지수 "
        f"<span style='font-size:14px;color:#9AA5B1'>— {disp} 공약이 조례·예산·집행까지 이어졌나</span>",
        unsafe_allow_html=True)
    st.caption(f"인천 10개 군·구의 '{fld}' 분야를 공약→입법→예산→집행 4트랙으로 교차 추적 "
               f"({sub}). 지역현안 특화 대시보드.")

    # 예산/집행 = 실 2025 결산(districts_all l2), 조례·의안 = domain_status 트랙(입법 정본) 병합
    #  (2026-06-14: 기존 domain_status 데모 예산/집행이 실데이터와 불일치 → 실 l2로 통일).
    recs = []
    for gu, slug in _INCHEON_GU:
        d = _domain_status_slug(slug)
        trk = next((x for x in d.get("분야", []) if x["분야"] == fld), None)   # 조례·의안 트랙
        rg = district_rec("인천광역시", gu)
        fb = next((x for x in (rg["l2"] if rg else []) if x["분야"] == fld), None)  # 실 예산/집행
        if fb:
            recs.append({"구": gu, "slug": slug,
                         "예산_억": fb.get("예산_억", 0), "집행_억": fb.get("집행_억", 0),
                         "집행률": fb.get("집행률", 0),
                         "조례수": (trk or {}).get("조례수", 0), "의안수": (trk or {}).get("의안수", 0)})
    if not recs:
        st.info("데이터 준비 중입니다."); return

    tot_b = sum(r.get("예산_억", 0) for r in recs)
    tot_e = sum(r.get("집행_억", 0) for r in recs)
    rate = tot_e / tot_b * 100 if tot_b else 0
    n_ord = sum(r.get("조례수", 0) for r in recs)
    n_bill = sum(r.get("의안수", 0) for r in recs)
    anomaly = [r for r in recs if r.get("집행률", 0) < 80 or r.get("집행률", 0) > 105]

    k = st.columns(4)
    k[0].metric(f"인천 {disp} 예산(10구)", f"{tot_b:,.0f}억")
    k[1].metric("평균 집행률", f"{rate:.0f}%")
    k[2].metric(f"{disp} 조례·의안", f"{n_ord:,}·{n_bill:,}건")
    k[3].metric("⚠️ 집행 이상치 구", f"{len(anomaly)}곳")

    st.markdown(f"**🏙️ 군·구별 {disp} 예산·집행 비교** "
                "<span style='color:#9AA5B1;font-size:12px'>(막대 클릭 → 해당 구 4트랙 상세)</span>",
                unsafe_allow_html=True)
    _field_chart(recs, "구", f"theme_{theme}", click=_open_gu_theme, topn=10)

    if anomaly:
        items = "".join(
            f"<li><b>{r['구']}</b> — {disp} 집행률 <b>{r['집행률']:.0f}%</b> "
            + ("⚠️ 미집행(예산 편성됐으나 80% 미만 집행 — 토지보상·착공지연 등 점검 필요)"
               if r['집행률'] < 80 else
               "🔺 추경·이월(편성 초과 집행 — 사업 변경·추경 점검)")
            + "</li>"
            for r in sorted(anomaly, key=lambda x: x['집행률']))
        st.markdown(
            f"<div style='background:#FCF3F0;border-left:4px solid #C0552B;border-radius:8px;"
            f"padding:9px 13px;margin:6px 0;font-size:13px'>"
            f"🚨 <b>{disp} 예산 집행 이상치 경고등</b> "
            f"<span style='color:#9AA5B1;font-size:12px'>(편성↔결산 교차검증 · 정상 80~105% 범위 밖)</span>"
            f"<ul style='margin:5px 0 0;padding-left:20px;color:#52606D'>{items}</ul></div>",
            unsafe_allow_html=True)
    else:
        st.caption("✅ 10개 군·구 모두 정상 집행 범위(80~105%) 내")

    st.markdown(f"**🔗 군·구별 {disp} 4트랙 사슬 완성도** "
                "<span style='color:#9AA5B1;font-size:12px'>(공약→입법→예산→집행이 끊김 없이 이어졌는가)</span>",
                unsafe_allow_html=True)
    head = ("<tr style='background:#EEF3F7;color:#2C5F6F'>"
            "<th style='padding:5px 8px;text-align:left'>군·구</th>"
            "<th>조례</th><th>의안</th><th>예산(억)</th><th>집행률</th><th>나라장터 계약</th></tr>")
    body = []
    for r in sorted(recs, key=lambda x: -x.get("예산_억", 0)):
        rt = r.get("집행률", 0)
        dot = "#3FA66A" if 80 <= rt <= 105 else ("#C0552B" if rt < 80 else "#E8A13B")
        cc = _theme_contract_count(r["slug"], fld)
        ccs = (f"{cc['수']}건·{cc['금액']:.1f}억" if cc and cc["수"] else
               ("—" if cc is not None else "수집예정"))
        body.append(
            f"<tr style='border-bottom:1px solid #EEF1F4'>"
            f"<td style='padding:4px 8px;font-weight:600'>{r['구']}</td>"
            f"<td style='text-align:center'>{r.get('조례수',0)}</td>"
            f"<td style='text-align:center'>{r.get('의안수',0)}</td>"
            f"<td style='text-align:center'>{r.get('예산_억',0):,.0f}</td>"
            f"<td style='text-align:center'><span style='color:{dot};font-weight:700'>{rt:.0f}%</span></td>"
            f"<td style='text-align:center;color:#52606D'>{ccs}</td></tr>")
    st.markdown(
        f"<table style='width:100%;border-collapse:collapse;font-size:12.5px'>{head}{''.join(body)}</table>",
        unsafe_allow_html=True)
    st.caption("※ 나라장터 계약 = 계약명 키워드로 분야 매칭(구 전체 계약 중). '수집예정'=해당 구 수집 진행 중. "
               "조례·의안은 발의·개정 포함. 데이터: 지방재정365·CLIK·law.go.kr·나라장터.")

    # 입법 발의 구조(집행부 vs 의회) — 중립 구조 지표(개인 의원 평가·등급 아님)
    tot_bill = sum(r.get("의안수", 0) for r in recs)
    exec_b = sum(r.get("의안단체장", 0) for r in recs)
    mem_b = sum(r.get("의안의원", 0) for r in recs)
    etc_b = max(tot_bill - exec_b - mem_b, 0)
    if tot_bill:
        ep, mp, tp = (exec_b / tot_bill * 100, mem_b / tot_bill * 100, etc_b / tot_bill * 100)
        st.markdown(
            f"**🏛️ 인천 {disp} 입법 발의 구조** "
            "<span style='color:#9AA5B1;font-size:12px'>(누가 발의했나 — 집행부(단체장) vs 의회(의원). "
            "발의 건수 집계이며 개인 의원 평가가 아님)</span>", unsafe_allow_html=True)
        seg = ("<div style='display:flex;height:26px;border-radius:6px;overflow:hidden;"
               "font-size:11px;color:#fff;font-weight:600'>"
               f"<div style='width:{ep:.1f}%;background:#2C5F6F;text-align:center;line-height:26px'>집행부 {ep:.0f}%</div>"
               f"<div style='width:{mp:.1f}%;background:#2C7FB8;text-align:center;line-height:26px'>의원 {mp:.0f}%</div>"
               + (f"<div style='width:{tp:.1f}%;background:#9AA5B1;text-align:center;line-height:26px'>기타 {tp:.0f}%</div>"
                  if tp > 1.5 else "") + "</div>")
        st.markdown(seg, unsafe_allow_html=True)
        lead = "집행부(단체장)" if exec_b >= mem_b else "의회(의원)"
        st.caption(f"인천 {disp} 의안 {tot_bill:,}건 = 집행부 {exec_b:,} · 의원 {mem_b:,} · 기타 {etc_b:,}. "
                   f"{disp} 입법은 {lead} 발의가 다수 — 자치입법 활성화 관점의 중립 구조 지표. 데이터: CLIK 지방의정포털.")

    _render_perf_panel(theme)


@st.cache_data
def _incheon_perf(theme):
    """인천 관내 공공기관 성과 데이터(collect_incheon_perf.py 산출)."""
    fn = "incheon_perf_transport.json" if theme == "transport" else "incheon_perf_env.json"
    p = POC / fn
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _render_perf_panel(theme):
    """⑤ 성과(Outcome) 트랙 — 인천 관내 공공기관 실측으로 '예산이 성과가 됐는가' 검증.
    교통=인천교통공사 역별 이용 / 환경=인천환경공단 하수처리 수질 제거율."""
    d = _incheon_perf(theme)
    if not d:
        return
    st.markdown(
        "<div style='margin-top:14px'></div>"
        "**🎯 ⑤ 성과(Outcome) — 그 예산이 실제 성과로 이어졌나** "
        "<span style='color:#9AA5B1;font-size:12px'>(집행 다음 단계 · 인천 관내 공공기관 실측 데이터)</span>",
        unsafe_allow_html=True)

    if theme == "transport":
        k = st.columns(3)
        k[0].metric("도시철도 일평균 이용", f"{d['일평균_전체']:,}명")
        k[1].metric("운영 역", f"{d['역수']}개역")
        k[2].metric("노선", f"{len(d['호선별'])}개")
        lr = "".join(
            f"<span style='display:inline-block;margin:2px 10px 2px 0;font-size:12.5px'>"
            f"{x['호선']} <b style='color:#2C5F6F'>{x['일평균이용']:,}명/일</b></span>"
            for x in d["호선별"])
        st.markdown(lr, unsafe_allow_html=True)
        top = d["상위역"][:8]
        mx = max(x["일평균이용"] for x in top) or 1
        bars = "".join(
            f"<div style='display:flex;align-items:center;margin:2px 0;font-size:12px'>"
            f"<span style='width:74px;color:#52606D'>{x['역명']}</span>"
            f"<span style='background:#2C7FB8;height:12px;width:{x['일평균이용']/mx*170:.0f}px;"
            f"border-radius:3px;margin-right:6px'></span>"
            f"<span style='color:#52606D'>{x['일평균이용']:,}</span></div>"
            for x in top)
        st.markdown(f"<div style='margin-top:4px;font-size:12.5px;color:#2C5F6F'><b>이용 상위 역(일평균)</b></div>{bars}",
                    unsafe_allow_html=True)
        st.caption(f"💡 인천 교통 예산이 실제 시민 이동으로 — 도시철도 하루 평균 **{d['일평균_전체']:,}명**이 이용. "
                   f"역세권 교통 투자의 성과를 이용 추이로 검증 가능. 기간 {d['기간']}. 출처: {d['출처']}.")
    else:
        ms = [m for m in d["metrics"] if m.get("제거율") is not None]
        if ms:
            cols = st.columns(min(len(ms), 4))
            for i, m in enumerate(ms[:4]):
                cols[i].metric(m["항목"].split("(")[0], f"{m['제거율']:.0f}%",
                               help=f"유입 {m['유입']} → 방류 {m['방류']} {m['단위']}")
        bars = ""
        for m in ms:
            bars += (f"<div style='margin:5px 0;font-size:12px'>"
                     f"<b>{m['항목']}</b> 제거율 <b style='color:#2E9E5B'>{m['제거율']:.0f}%</b> "
                     f"<span style='color:#9AA5B1'>(유입 {m['유입']} → 방류 {m['방류']} {m['단위']})</span>"
                     f"<div style='background:#EDF1F4;border-radius:4px;height:9px;margin-top:2px'>"
                     f"<div style='background:#2E9E5B;height:9px;width:{min(m['제거율'],100):.0f}%;"
                     f"border-radius:4px'></div></div></div>")
        st.markdown(bars, unsafe_allow_html=True)
        st.caption(f"💡 인천 환경 예산이 실제 수질 개선으로 — **{d['사업소']}** 하수처리가 유입 오염물질을 "
                   f"평균 70~97% 제거해 방류. 환경 투자의 성과를 방류수질로 검증. 기간 {d['기간']}. 출처: {d['출처']}.")


def _exec_pie(budget, exe, key):
    """총예산 대비 집행률 — 원형(파이) 차트(도넛 아님). 고정."""
    import plotly.graph_objects as go
    exe = max(0, exe); rem = max(budget - exe, 0)
    rate = round(exe / budget * 100) if budget else 0
    fig = go.Figure(go.Pie(values=[exe, rem], labels=["집행", "미집행"],
                           marker_colors=["#2C7FB8", "#DCE3E9"], hole=0, sort=False,
                           direction="clockwise", textinfo="label+percent",
                           textposition="inside", insidetextorientation="horizontal"))
    fig.update_layout(height=280, margin=dict(l=4, r=4, t=36, b=4), showlegend=False,
                      title=dict(text=f"총예산 대비 집행률 {rate}%", x=0.5, xanchor="center",
                                 font=dict(size=15, color="#2C5F6F")))
    st.plotly_chart(fig, key=key, use_container_width=True,
                    config={"staticPlot": True, "displayModeBar": False})


def _source_bar(r, key):
    """L5 — 세부사업 재원 구성(국비·시도비·시군구비·기타) 가로 스택 막대."""
    import plotly.graph_objects as go
    parts = [("국비", r.get("국비_억", 0), "#2C5F6F"), ("시도비", r.get("시도비_억", 0), "#2C7FB8"),
             ("시군구비", r.get("시군구비_억", 0), "#7FB4D0"), ("기타", r.get("기타_억", 0), "#DCE3E9")]
    parts = [p for p in parts if p[1] > 0]
    if not parts:
        st.caption("재원 구성 데이터 없음"); return
    fig = go.Figure()
    for name, val, col in parts:
        fig.add_bar(y=["재원"], x=[val], orientation="h", name=name, marker_color=col,
                    text=[f"{name} {val:,.0f}억"], textposition="inside", insidetextanchor="middle",
                    hovertemplate=f"{name} %{{x:,.1f}}억<extra></extra>")
    fig.update_layout(barmode="stack", height=120, margin=dict(l=0, r=8, t=4, b=0),
        showlegend=True, legend=dict(orientation="h", yanchor="top", y=-0.1, font=dict(size=11)),
        plot_bgcolor="white", dragmode=False, bargap=0.4,
        xaxis=dict(ticksuffix="억", fixedrange=True, showgrid=True, gridcolor="#F1F4F7", zeroline=False),
        yaxis=dict(showticklabels=False, fixedrange=True))
    st.plotly_chart(fig, key=key, use_container_width=True,
                    config={"staticPlot": True, "displayModeBar": False})


def _l4_detail(rows, dom):
    """L4 세부사업 상세(집행률 원형 + 예산/집행/잔액) + L5 재원 구성(국비/시도비/시군구비/기타)."""
    seb = _l4()
    st.button("◂ 세부사업 목록", on_click=clear_l4, key="back_l4")
    r = next((x for x in rows if x.get("세부사업") == seb), None)
    if not r:
        st.info("세부사업 데이터 없음"); return
    b = r.get("예산_억", 0); e = r.get("집행_억", 0)
    rate = r.get("집행률", 0) or (e / b * 100 if b else 0)
    st.markdown(f"#### 💰 {seb}")
    bumun = f" · 부문 {r['부문']}" if r.get("부문") else ""
    hoegye = f" · {r['회계']}" if r.get("회계") else ""
    st.caption(f"분야 {dom}{bumun}{hoegye}")
    c1, c2 = st.columns([1, 1.6])
    with c1:
        _exec_pie(b, e, f"l4pie_{seb}")
    with c2:
        m = st.columns(2)
        m[0].metric("예산현액", f"{b:,.1f}억")
        m[1].metric("집행(지출)", f"{e:,.1f}억")
        m2 = st.columns(2)
        m2[0].metric("잔액", f"{max(b - e, 0):,.1f}억")
        m2[1].metric("집행률", f"{rate:.0f}%")

    # 저집행 안내 — '미집행 = 낭비' 오해 방지(이월·전환과 불용은 결산상 구분 안 됨)
    if not r.get("_rollup") and rate < 50:
        st.markdown(
            f"<div style='background:#FBF7EF;border-left:4px solid #C99A3B;border-radius:8px;"
            f"padding:8px 12px;margin:6px 0;font-size:12px;color:#6B5A2E'>"
            f"ⓘ <b>집행률 {rate:.0f}%</b> — 미집행분은 사업 지연으로 <b>다음 해로 이월</b>되거나 예산이 "
            f"<b>전환</b>된 경우가 많아, 곧바로 ‘미사용·낭비’로 보기는 어렵습니다. "
            f"(결산 데이터에는 이월·전환·불용을 구분하는 항목이 없어 단정은 불가합니다.)</div>",
            unsafe_allow_html=True)
    if r.get("_rollup"):
        st.caption("ⓘ ‘기타 N개 사업’은 상위 표시 외 나머지 세부사업을 합친 묶음입니다(개별 내역은 향후 확장).")

    # L5 — 재원 구성
    has_src = any(r.get(k, 0) for k in ("국비_억", "시도비_억", "시군구비_억", "기타_억"))
    if has_src:
        st.markdown("##### 💧 재원 구성 — 이 사업의 예산은 어디서 왔나")
        _source_bar(r, f"l5src_{seb}")
        st.caption("국비(중앙정부) · 시도비(광역) · 시군구비(자치구 자체) · 기타. "
                   "ⓘ 세부사업·재원이 지방재정 공개의 최소 단위입니다(이하 세목·계약은 향후 확장).")
    else:
        st.caption("ⓘ 세부사업은 지방재정 공개의 최소 단위입니다.")


def _domain_cards_g(sido, gu, rec, cols_n=2):
    """전 자치구 공통 분야(L2) 카드 → 세부사업(L3) drill. 강남과 동일 레이아웃."""
    ds = _dstat(sido, gu)
    cols = st.columns(cols_n)
    for i, x in enumerate(rec["l2"][:14]):
        rate = x.get("집행률", 0)
        row = ds.get(x["분야"])
        dot = SIG.get(_sig(rate), "#9AA5B1")   # 신호등 = 실 2025 집행률 기반(domain_status 데모 신호 stale, 2026-06-14)
        ord_txt = (f" · 조례 {row['조례수']}" + (f" · 의안 {row['의안수']}" if row.get("의안수") else "")) if row else ""
        with cols[i % cols_n]:
            st.markdown(
                f"<div style='border:1px solid #E4E7EB;border-left:5px solid {dot};"
                f"border-radius:8px;padding:10px 12px;margin-bottom:8px'>"
                f"<b style='color:#2C5F6F'>● {x['분야']}</b><br>"
                f"<span style='font-size:12px;color:#52606D'>"
                f"예산 {x['예산_억']:,.0f}억 · 집행률 {rate:.0f}%{ord_txt}</span></div>",
                unsafe_allow_html=True)
            st.button("이행 현황 보기 ▸", key=f"dom_{i}", on_click=go_dom, args=(x["분야"],))


def _domain_detail_g(sido, gu):
    dom = _dom()
    biz = district_l3().get(f"{sido}|{_parent_gu(gu)}", {}).get(dom, [])
    if _l4():
        _l4_detail(biz, dom); return
    st.button("◂ 분야 목록", on_click=clear_dom, key="back_dom")
    rec = district_rec(sido, gu)
    fld = next((x for x in (rec["l2"] if rec else []) if x["분야"] == dom), None)
    row = _dstat(sido, gu).get(dom)
    # 신호등 = 실 2025 집행률(fld) 기반(domain_status 데모 신호 stale, 2026-06-14)
    # 집행률·신호등 = 표시 금액(예산_억/집행_억) 기준으로 계산 → 아래 세부사업 막대(동일 금액)와 100% 일치
    #  (2026-06-14: 집행률 필드(정밀)와 표시 금액의 0.1억 양자화 차이로 메트릭↔차트 1%p 어긋나던 것 통일).
    f_rate = (fld["집행_억"] / fld["예산_억"] * 100) if fld and fld.get("예산_억") else 0
    dot = SIG.get(_sig(f_rate) if fld else "amber", "#9AA5B1")
    st.markdown(f"#### <span style='color:{dot}'>●</span> {dom} — 정책 이행 현황",
                unsafe_allow_html=True)
    if fld:
        if _is_full_chain(sido, gu) and row and "의안수" in row:
            k = st.columns(5)
            k[0].metric("관련 공약", f"{row.get('공약수', 0)}건")
            k[1].metric("제정 조례", f"{row['조례수']}건")
            k[2].metric("발의 의안", f"{row['의안수']}건")
            k[3].metric("분야 예산", f"{fld['예산_억']:,.0f}억")
            k[4].metric("집행률", f"{f_rate:.0f}%")
            st.caption(f"💰 총예산 {fld['예산_억']:,.0f}억 · 실제 집행 {fld['집행_억']:,.0f}억 (2025 결산)")
            srcs = row.get("공약출처") or []
            if row.get("공약수", 0) and srcs:
                from data_provenance import badge
                tagged = []
                for s in srcs:
                    g = "A" if "선관위" in s else "B"
                    tagged.append(f"{badge(g, g)} {s}")
                st.markdown(
                    "<div style='font-size:12px;color:#52606D'>🔗 공약 출처(교차검증): "
                    + " · ".join(tagged) + "</div>", unsafe_allow_html=True)
        else:
            k = st.columns(4)
            k[0].metric("제정 조례", f"{row['조례수']}건" if row else "—")
            k[1].metric("분야 예산", f"{fld['예산_억']:,.0f}억")
            k[2].metric("분야 집행", f"{fld['집행_억']:,.0f}억")
            k[3].metric("집행률", f"{f_rate:.0f}%")

    # 3개년 예산 추이 병기: 2024 결산 → 2025 결산 → 2026 편성
    b24 = _b2024_field(sido, gu, dom) if fld else None
    b26 = _b2026_field(sido, gu, dom) if fld else None
    if fld and (b24 or b26 is not None):
        parts = []
        if b24:
            parts.append(f"<b>2024 결산</b> {b24['예산_억']:,.0f}억"
                         f"<span style='color:#9AA5B1'> 집행 {b24['집행률']:.0f}%</span>")
        parts.append(f"<b>2025 결산</b> {fld['예산_억']:,.0f}억"
                     f"<span style='color:#9AA5B1'> 집행 {f_rate:.0f}%</span>")   # 금액 기준 통일
        if b26 is not None:
            parts.append(f"<b style='color:#1F5C7A'>2026 편성</b> {b26:,.0f}억")
        st.markdown(
            f"<div style='background:#EEF5FB;border-left:4px solid #2C7FB8;border-radius:8px;"
            f"padding:8px 12px;margin:6px 0;font-size:13px;color:#2C5F6F'>"
            f"📈 <b>예산 추이</b> &nbsp; " + " &nbsp;→&nbsp; ".join(parts)
            + "<br><span style='font-size:11px;color:#9AA5B1'>2026년은 편성(계획)이며, "
            "집행률은 회계연도 종료 후 결산에서 확정됩니다.</span></div>",
            unsafe_allow_html=True)

    full_slug = _is_full_chain(sido, gu)
    # 입법① — 최근 공포 조례
    if row is not None:
        st.markdown("**입법 ① 공포 조례** <span style='color:#9AA5B1;font-size:12px'>(law.go.kr 최종 조례)</span>"
                    if full_slug else "**입법 — 최근 제정·개정 조례**", unsafe_allow_html=True)
        if row.get("최근조례"):
            for o in row["최근조례"]:
                g = o.get("공포일자", ""); g = f"{g[:4]}.{g[4:6]}.{g[6:8]}" if len(g) == 8 else g
                st.markdown(f"- 📜 {o['조례명']} <span style='color:#9AA5B1;font-size:12px'>"
                            f"({o.get('제개정','')} {g})</span>", unsafe_allow_html=True)
        else:
            st.caption("이 분야 조례 미확인")
        if full_slug:
            _render_ordinance_crosscheck(sido, gu)

    # 입법② — 지방의회 발의 의안 (풀체인 자치구: CLIK 의안 + 발의주체)
    if row is not None and full_slug:
        _render_bills_section(row)
        _render_minutes_crosscheck(sido, gu)

    # 예산 → 집행 (세부사업) — 막대 차트(클릭 → L4 상세)
    st.markdown("**예산 → 집행 (세부사업)** <span style='color:#9AA5B1;font-size:12px'>막대 클릭 → 세부 상세</span>",
                unsafe_allow_html=True)
    if full_slug:
        _render_budget_crosscheck(sido, gu, dom)
        _render_contract_crosscheck(sido, gu)
    _field_chart(biz, "세부사업", f"l3_{sido}_{_parent_gu(gu)}_{dom}", click=go_l4)

    # AI 갭 분석·제언
    if row is not None:
        st.markdown(
            f"<div style='background:#F2F7F8;border-left:4px solid #2C5F6F;"
            f"border-radius:8px;padding:12px 15px;margin-top:8px'>"
            f"🤖 <b>AI 갭 분석 — 남은 과제</b><br><span style='font-size:14px;color:#52606D'>"
            + "<br>".join(f"• {g}" for g in row.get("남은과제", [])) +
            f"</span><br><br>🤖 <b>AI 제언</b><br><span style='font-size:14px;color:#52606D'>"
            + "<br>".join(f"→ {r}" for r in row.get("AI제언", [])) + "</span></div>",
            unsafe_allow_html=True)
        if not full_slug:
            st.caption("※ 공약·의안 트랙은 강남구·인천 서구 시연 중(선관위 단체장 공약·CLIK 의안 수집 후 전국 확장).")


@st.cache_data
def _elected_data():
    try:
        return json.loads((POC / "elected_2026.json").read_text(encoding="utf-8"))
    except Exception:
        return {"광역": {}, "기초": {}}


_PARTY_COLOR = {"더불어민주당": "#152484", "국민의힘": "#E61E2B", "조국혁신당": "#06275E",
                "개혁신당": "#FF7210", "진보당": "#D6001C", "무소속": "#7B8794"}


def _render_elected(sido, gu=None):
    """2026 지방선거(제9회·6/3) 당선자 + 핵심 공약 카드.
    gu=None → 광역단체장(시·도지사) / gu 지정 → 기초단체장(구청장·시장·군수).
    데이터 없으면 렌더 안 함(graceful)."""
    data = _elected_data()
    rec = (data.get("기초", {}).get(f"{sido}|{gu}") if gu
           else data.get("광역", {}).get(sido))
    if not rec or not rec.get("name"):
        return
    party = rec.get("정당", "")
    col = _PARTY_COLOR.get(party, "#2C5F6F")
    title = rec.get("직책") or ("시·도지사" if not gu else "구청장·시장·군수")
    src = rec.get("출처", "중앙선거관리위원회")
    items = ""
    for p in (rec.get("공약") or [])[:5]:
        fld = p.get("분야", "")
        tag = (f"<span style='background:#EAF4FB;color:#2C5F6F;border-radius:8px;"
               f"padding:1px 7px;font-size:11px;margin-right:6px'>{fld}</span>" if fld else "")
        items += f"<li style='margin:3px 0'>{tag}{p.get('제목','')}</li>"
    pledge_html = (f"<ul style='margin:6px 0 0;padding-left:18px;color:#3A4754;font-size:13.5px'>{items}</ul>"
                   if items else "<div style='color:#9AA5B1;font-size:13px;margin-top:4px'>등록 공약 준비 중</div>")
    st.markdown(
        f"<div style='background:#F8FAFC;border:1px solid #E1E8EE;"
        f"border-radius:10px;padding:13px 16px;margin:8px 0'>"
        f"<div style='font-size:13px;color:#7B8794'>🏛️ {title} 당선자 "
        f"<span style='color:#9AA5B1'>· 제9회 지방선거(2026.6.3)</span></div>"
        f"<div style='font-size:18px;font-weight:800;color:#1F4654;margin:2px 0 1px'>{rec['name']} "
        f"<span style='font-size:13px;font-weight:700;color:{col}'>{party}</span></div>"
        f"<div style='font-size:12.5px;color:#52606D;font-weight:700;margin-top:7px'>📋 핵심 공약</div>"
        f"{pledge_html}"
        f"<div style='font-size:11px;color:#9AA5B1;margin-top:7px'>출처: {src}</div>"
        f"</div>", unsafe_allow_html=True)


def _render_gu(mode):
    sido, gu = _sido(), _gu()
    top = st.columns([1, 1, 5])
    top[0].button("◂ 전국", on_click=go_home, key="g_nation")
    top[1].button(f"◂ {sido}", on_click=go_sido, args=(sido,), key="g_sido")

    rec = district_rec(sido, gu)
    if not rec:
        st.info(f"{gu}의 예산 데이터는 확장 예정입니다."); return
    is_gn = (gu == "강남구")
    is_full = bool(_is_full_chain(sido, gu))
    disp = _parent_gu(gu); l1 = rec["l1"]
    grade, gdesc = _transparency_grade(sido, gu)
    gcol = _GRADE_COLOR[grade]
    title = "정책 이행" if is_full else "세출 → 세부사업"
    st.markdown(
        f"#### 🏛️ {sido} {disp} "
        f"<span style='background:{gcol};color:#fff;font-size:13px;border-radius:6px;"
        f"padding:2px 8px;vertical-align:middle'>투명성 {grade}</span> "
        f"<span style='font-size:13px;color:#9AA5B1'>— 분야별 {title}</span>",
        unsafe_allow_html=True)
    st.caption(f"📊 데이터 공개 등급 {grade} · {gdesc}")

    if _dom():
        if is_gn:
            _domain_detail()
        else:
            _domain_detail_g(sido, gu)
        return

    # L2 정리 레이아웃 — 한 줄: [행정동 경계(가장 큼) | 집행률 원형(중간) | 요약(작음)] → 아래 분야 막대
    a, b, c = st.columns([1.15, 1.0, 0.7])
    with a:
        _emd_image(sido, gu)
    with b:
        _exec_pie(l1["총예산_억"], l1["총집행_억"], f"pie_{sido}_{disp}")
    with c:
        st.metric("총예산 (2025결산)", f"{l1['총예산_억']:,.0f}억")
        st.metric("총집행 (2025)", f"{l1['총집행_억']:,.0f}억")
        b26t = _b2026_total(sido, gu)
        if b26t is not None:
            st.metric("2026 편성", f"{b26t:,.0f}억",
                      delta=f"{b26t - l1['총예산_억']:,.0f}억 vs 2025")
        else:
            st.metric("사업수", f"{l1['사업수']:,}")
        if disp != gu:
            st.caption(f"※ 일반구({gu})=소속 시({disp}) 기준")
    # 분야(L2) 막대 = 실 2025 결산(rec["l2"]) 통일. 강남도 domain_status 데모(stale 예산/집행) 대신 실데이터 사용
    #  (2026-06-14: domain_status는 입법 트랙(공약·조례·의안)만 정본, 예산/집행은 districts_all/district_l3 실데이터).
    fields = rec["l2"]
    _field_chart(fields, "분야", f"l2_{sido}_{disp}", click=go_dom)
    # (2026-06-13) '🔍 우리 동네 예산 찾기' expander 제거 — 팀장 요청.
    _render_elected(sido, gu)         # 기초단체장(구청장·시장·군수) 2026 당선자 + 공약


@st.cache_data
def _ai_districts():
    p = POC / "ai_insights_seoul25.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _ai_exec_rates(sido):
    """자치구별 종합 집행률(district_l3, 전 시도 보유). [(자치구, 집행률%)]."""
    l3 = district_l3()
    out, seen = [], False
    for key, doms in l3.items():
        if not key.startswith(sido + "|"):
            continue
        gu = key.split("|", 1)[1]
        if sido == "제주특별자치도":
            if seen:
                continue
            seen = True
            gu = "제주도(통합)"
        b = sum((r.get("예산_억", 0) or 0) for rows in doms.values() for r in rows)
        e = sum((r.get("집행_억", 0) or 0) for rows in doms.values() for r in rows)
        if b:
            out.append((gu, round(e / b * 100, 1)))
    return out


def _ai_seoul_col(col):
    d = _ai_districts()
    return [(x["district"], x.get(col, 0) or 0) for x in d["districts_25"]] if d else []


def _ai_incheon_suui():
    """인천 자치구 수의계약 비율(나라장터 표본). [(자치구, %)]."""
    out = []
    for k, slug in FULL_CHAIN.items():
        if not k.startswith("인천광역시|"):
            continue
        c = _contracts(slug)
        if c and c.get("수의계약비율") is not None:
            out.append((k.split("|", 1)[1], c["수의계약비율"]))
    return out


def _ai_metrics(sido):
    """시도별 가용 지표. (id, 라벨, 설명, kind['ratio'|'exec'], data[[(구,값)]])."""
    M = []
    if sido == "서울특별시" and _ai_districts():
        M += [("suui", "🤝 수의계약 비율", "경쟁입찰 없이 맺은 계약 비중(%) — 높을수록 점검", "ratio", _ai_seoul_col("수의계약비율")),
              ("upchu", "💳 업무추진비 지표", "기관 운영성 경비 상대지표 — 25구 비교", "ratio", _ai_seoul_col("업무추진비")),
              ("haengsa", "🎪 행사·축제 경비", "행사성 지출 비중(%)", "ratio", _ai_seoul_col("행사축제경비비율"))]
    elif sido == "인천광역시":
        ic = _ai_incheon_suui()
        if ic:
            M.append(("suui", "🤝 수의계약 비율", "경쟁입찰 없이 맺은 계약 비중(%)·나라장터 표본", "ratio", ic))
    M.append(("exec", "📊 자치구 집행률", "예산 대비 실제 집행(%) — 80~105% 정상, 벗어나면 이상치", "exec", _ai_exec_rates(sido)))
    return [m for m in M if m[4]]


def _render_ai_insights():
    """🤖 AI 자동 발견 — 선택 시도의 자치구를 시민감시 지표로 비교·이상치 자동 하이라이트(시도별 일반화)."""
    sido = _sido()
    if not sido:
        _nav({}); return
    metrics = _ai_metrics(sido)
    st.button(f"◂ {sido} 지도", on_click=lambda: _nav({"sido": sido}), key="ai_back")
    if not metrics:
        st.info("이 시도는 AI 자동 발견 지표 데이터가 아직 없습니다."); return
    st.markdown(f"### 🤖 AI 자동 발견 — {sido} 자치구 시민감시 지표 "
                "<span style='font-size:14px;color:#9AA5B1'>— AI가 이상 신호를 스스로 짚습니다</span>",
                unsafe_allow_html=True)

    labels = [m[1] for m in metrics]
    pick = st.segmented_control("지표", labels, default=labels[0],
                                key="ai_metric", label_visibility="collapsed") or labels[0]
    _, label, desc, kind, data = next((m for m in metrics if m[1] == pick), metrics[0])
    st.caption(desc + " · 데이터: 지방재정365·나라장터")
    if not data:
        st.info("데이터 없음"); return

    avg = sum(v for _, v in data) / len(data)
    sv = sorted(data, key=lambda x: -x[1])
    name = label.split(" ", 1)[-1]

    import plotly.graph_objects as go
    asc = sv[::-1]
    names = [g for g, _ in asc]
    nums = [round(v, 1) for _, v in asc]
    colors = []
    for g, v in asc:
        if kind == "exec":
            colors.append("#C0552B" if v < 80 else "#E8A13B" if v > 105 else "#3FA66A")
        else:
            colors.append("#C0552B" if g == sv[0][0] else "#3FA66A" if g == sv[-1][0]
                          else "#E8A13B" if v >= avg else "#C9D2DA")
    fig = go.Figure(go.Bar(x=nums, y=names, orientation="h", marker_color=colors,
                           text=[f"{v}" for v in nums], textposition="outside", cliponaxis=False))
    fig.add_vline(x=(100 if kind == "exec" else avg), line_dash="dash", line_color="#52606D",
                  annotation_text=("정상 100" if kind == "exec" else f"평균 {avg:.1f}"),
                  annotation_position="top right")
    fig.update_layout(height=max(360, 23 * len(data) + 70), margin=dict(l=8, r=40, t=22, b=8),
                      plot_bgcolor="white", showlegend=False, font=dict(size=12))
    fig.update_xaxes(showgrid=True, gridcolor="#EEF1F4", title=None)
    fig.update_yaxes(title=None)
    st.plotly_chart(fig, key="ai_chart", use_container_width=True,
                    config={"displayModeBar": False, "staticPlot": True})

    if kind == "exec":
        bad = [(g, v) for g, v in sv if v < 80 or v > 105]
        if bad:
            items = " · ".join(f"{g} {v:.0f}%" for g, v in bad[:6])
            st.markdown(
                f"<div style='background:#FCF3F0;border-left:4px solid #C0552B;border-radius:8px;"
                f"padding:11px 14px;margin:6px 0;font-size:13.5px;line-height:1.7'>"
                f"🤖 <b>AI 자동 인사이트</b><br>🚨 집행률이 정상범위(80~105%)를 벗어난 자치구 "
                f"<b>{len(bad)}곳</b>: {items}. 미집행·과집행 점검이 필요합니다.</div>",
                unsafe_allow_html=True)
        else:
            st.caption("✅ 모든 자치구가 정상 집행 범위(80~105%) 내 — 양호합니다.")
    else:
        top_g, top_v = sv[0]; bot_g, bot_v = sv[-1]
        ratio = top_v / avg if avg else 0
        st.markdown(
            f"<div style='background:#F5F7FA;border-left:4px solid #2C5F6F;border-radius:8px;"
            f"padding:11px 14px;margin:6px 0;font-size:13.5px;line-height:1.7'>"
            f"🤖 <b>AI 자동 인사이트</b><br>"
            f"🚨 <b>{top_g}</b> — {name} <b>{top_v:.1f}</b>, 최고(평균 {avg:.1f}의 <b>{ratio:.1f}배</b>). 점검 필요.<br>"
            f"✅ <b>{bot_g}</b>는 <b>{bot_v:.1f}</b>로 최저, 양호. "
            f"<span style='color:#9AA5B1'>— 잘한 곳과 점검할 곳을 데이터가 가립니다.</span></div>",
            unsafe_allow_html=True)
    st.caption("※ 시민이 분석할 줄 몰라도 자치구를 한 화면에서 비교해 AI가 이상 신호를 자동으로 짚어줍니다. "
               "개인·정파 평가가 아닌 자치단체 단위 지표 비교입니다.")


def run(mode):
    label = "A안 · 동·읍 경계 표시형" if mode == "A" else "B안 · 데이터 집중형"
    st.set_page_config(page_title=f"모정 프로토타입 {mode}안", page_icon="🏛️",
                       layout="wide", initial_sidebar_state="collapsed")
    # 뒤로가기(popstate) = 문서 리로드가 없어 Streamlit 스크립트가 재실행되지 않음 →
    # URL만 바뀌고 화면이 안 따라옴(L4→L3→L2 화면 그대로). popstate 시 명시적 reload로 재실행 강제.
    # 🔴 실측 검증(2026-06-11): 소프트 resync(pushState 후 숨은버튼 클릭)는 from_dict 내비(테마·버튼)의
    #    내부 query_params stale 때문에 back 시 콘텐츠가 동결되고, 전진 시 URL이 desync됨 → reload 가 유일 정답.
    # __mjPop 가드 = 리스너 1회만 등록(중복 시 1회 back에 다중 reload = '두 번' 체감 원인) → 방지.
    components.html(
        "<script>var w=window.parent;if(!w.__mjPop){w.__mjPop=1;"
        "w.addEventListener('popstate',function(){w.location.reload();});}</script>",
        height=0, width=0,
    )
    # 8501과 동일한 본문 폭·여백 + 입체 토글 스위치
    st.markdown("""<style>
      .block-container { padding-top: 2.6rem; max-width: 1240px; }
      #MainMenu, footer { visibility: hidden; }
      /* 스와이프 토글 — 왼쪽=ON, OFF=연한회색·ON=회색, 입체 노브 (선택자 다중 대비) */
      [role="switch"],
      [data-baseweb="checkbox"] > div:first-of-type {
        background-color: #E2E6EA !important;                 /* OFF = 연한 회색 (왼쪽 시작) */
        box-shadow: inset 0 1px 2px rgba(0,0,0,.16) !important;
        transition: background-color .2s ease !important;
      }
      [role="switch"][aria-checked="true"],
      [data-baseweb="checkbox"]:has(input:checked) > div:first-of-type {
        background-color: #9AA5B1 !important;                 /* ON = 회색 */
      }
      [role="switch"] > div,
      [data-baseweb="checkbox"] > div:first-of-type > div {   /* 노브 입체 */
        box-shadow: 0 1px 3px rgba(0,0,0,.30) !important;
        background-image: linear-gradient(180deg, #ffffff, #eef2f5) !important;
      }
    </style>""", unsafe_allow_html=True)
    _crumb()
    if _ai():
        _render_ai_insights()
    elif not _sido():
        _render_nation()
    elif _theme() and not _gu():
        _render_theme_detail()
    elif not _gu():
        _render_sido()
    else:
        _render_gu(mode)
    _render_footer()


def _render_footer():
    st.divider()
    _render_provenance_panel()
    st.markdown(
        "<div style='font-size:11px;color:#9AA5B1;line-height:1.7'>"
        "<b>ⓘ 출처표시</b> &nbsp;본 서비스는 공공누리 출처표시 조건에 따라 위 공공데이터를 활용하며, "
        "정부·공공기관의 공식 입장을 대변하지 않습니다. 언론 인용은 제목·출처·아웃링크만 노출(본문 비저장). "
        "집행률 100% 초과는 추경·이월 등 회계연도 내 예산 변경 반영분입니다."
        "</div>", unsafe_allow_html=True)


def _render_provenance_panel():
    """4트랙 데이터 출처·신뢰등급(A/B/C) + 교차검증 유형 투명성 레이어."""
    from data_provenance import (TRACK_SOURCES, GRADE, badge,
                                  MATCH_DISCLOSURE, TIMELAG_NOTICE)
    with st.expander("🔍 데이터 출처·신뢰등급 (다중소스 교차검증)", expanded=False):
        # 등급 범례
        legend = " &nbsp; ".join(
            f"{badge(g)} {name}" for g, (name, _c) in GRADE.items())
        st.markdown(f"<div style='font-size:12px;margin-bottom:6px'>{legend}</div>",
                    unsafe_allow_html=True)
        rows = ["<table style='width:100%;font-size:12px;border-collapse:collapse'>",
                "<tr style='color:#52606D;text-align:left'>"
                "<th>트랙</th><th>등급</th><th>1차 소스</th>"
                "<th>2차 교차검증 소스</th><th>유형</th><th>상태</th></tr>"]
        for t in TRACK_SOURCES:
            rows.append(
                "<tr style='border-top:1px solid #EEF1F4'>"
                f"<td style='padding:4px 6px;font-weight:600'>{t['트랙']}</td>"
                f"<td style='padding:4px 6px'>{badge(t['등급'], t['등급'])}</td>"
                f"<td style='padding:4px 6px'>{t['1차']}</td>"
                f"<td style='padding:4px 6px;color:#52606D'>{t['2차']}</td>"
                f"<td style='padding:4px 6px'>{t['교차검증']}</td>"
                f"<td style='padding:4px 6px;white-space:nowrap'>{t.get('상태','')}</td></tr>")
        rows.append("</table>")
        st.markdown("".join(rows), unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:11px;color:#9AA5B1;line-height:1.7;margin-top:8px'>"
            f"<b>🔗 트랙 연결 방식</b> &nbsp;{MATCH_DISCLOSURE}<br>"
            f"<b>⏱ 시점 안내</b> &nbsp;{TIMELAG_NOTICE}</div>",
            unsafe_allow_html=True)
