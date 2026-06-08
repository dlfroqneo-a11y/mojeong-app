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


@st.cache_data
def domain_status():
    p = POC / "domain_status_gangnam.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"분야": []}


@st.cache_data
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


# 만족도 색 스케일(발산형): 0점=파랑 → 1.25 연파랑 → 1.5 연빨강 → 5점 빨강
_SAT_ANCHORS = [(0.0, (0x64, 0x96, 0xFF)), (1.25, (0xD9, 0xE6, 0xFF)),
                (1.5, (0xFF, 0xE1, 0xE1)), (5.0, (0xFF, 0x5A, 0x5A))]


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


@st.cache_data
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
def _domain_cards(cols_n):
    ds = domain_status()["분야"]
    cols = st.columns(cols_n)
    for i, d in enumerate(ds):
        with cols[i % cols_n]:
            dot = SIG.get(d["신호"], "#9AA5B1")
            st.markdown(
                f"<div style='border:1px solid #E4E7EB;border-left:5px solid {dot};"
                f"border-radius:8px;padding:10px 12px;margin-bottom:8px'>"
                f"<b style='color:#2C5F6F'>● {d['분야']}</b><br>"
                f"<span style='font-size:12px;color:#52606D'>"
                f"공약 {d['공약수']} · 조례 {d['조례수']} · 의안 {d['의안수']}<br>"
                f"예산 {d['예산_억']:,.0f}억 · 집행률 {d['집행률']:.0f}%</span></div>",
                unsafe_allow_html=True)
            st.button("이행 현황 보기 ▸", key=f"dom_{i}",
                      on_click=go_dom, args=(d["분야"],))


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


def _render_budget_crosscheck(sido, gu, row):
    """예산 교차검증: 편성 예산서(cpl) ↔ 집행 결산(ep) 2단계 생애주기 병치 + 이상치."""
    pc = (_dstat_meta(sido, gu) or {}).get("예산교차") or {}
    if not pc or not pc.get("총예산_억"):
        return
    from data_provenance import badge
    rt = row.get("집행률", 0) if row else 0
    flag = ("✅ 편성대로 집행" if 80 <= rt <= 105 else
            ("⚠️ 미집행 점검" if rt < 80 else "🔺 추경·이월"))
    st.markdown(
        f"<div style='background:#F5F3F8;border-left:4px solid #7A5BA6;border-radius:8px;"
        f"padding:8px 12px;margin:4px 0;font-size:12px'>"
        f"🔗 <b>예산 교차검증</b> {badge('A','A')} "
        f"<span style='color:#52606D'>편성 예산서 ↔ 집행 결산 (lofin365 세출현황 — 사전 편성 vs 사후 결산 2단계 병치)</span><br>"
        f"<span style='color:#52606D'>이 분야 집행률 <b>{rt:.0f}%</b> {flag} · "
        f"자치구 전체 <b>{pc['전체집행률']:.0f}%</b> "
        f"(정합 {pc['정합분야']} · 미집행 {pc['미집행분야']} · 추경·이월 {pc['추경이월분야']} 분야)</span></div>",
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
    dot = SIG.get(d["신호"], "#9AA5B1")
    st.markdown(f"#### <span style='color:{dot}'>●</span> {d['분야']} — 정책 이행 현황",
                unsafe_allow_html=True)
    k = st.columns(4)
    k[0].metric("관련 공약", f"{d['공약수']}건")
    k[1].metric("제정 조례", f"{d['조례수']}건")
    k[2].metric("발의 의안", f"{d['의안수']}건")
    k[3].metric("예산 집행률", f"{d['집행률']:.0f}%")

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
    sc = st.columns(2)
    sc[0].button("📍 인천 서구 4트랙 보기 ▸", on_click=goto_district,
                 args=("인천광역시", "서구"), key="show_seo", width="stretch")
    sc[1].button("📍 서울 강남구 4트랙 보기 ▸", on_click=goto_district,
                 args=("서울특별시", "강남구"), key="show_gn", width="stretch")
    st.caption("🟢 인천 10개 자치구 전역 + 서울 강남구 = 4트랙 풀체인 완비. "
               "지도에서 인천을 클릭한 뒤 자치구를 선택하면 모든 인천 자치구를 볼 수 있습니다.")
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
        _render_vote(sat)
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
        if sido == "인천광역시":
            st.markdown("##### 🔥 인천 지역현안 킬러 테마 "
                        "<span style='font-size:13px;color:#9AA5B1'>— 공약이 예산·집행까지 이어졌는지 4트랙 교차 추적</span>",
                        unsafe_allow_html=True)
            tb = st.columns([1, 1, 3])
            tb[0].button("🌍 환경 투명성", on_click=go_theme, args=("env",),
                         key="theme_env", use_container_width=True)
            tb[1].button("🚌 교통 투명성", on_click=go_theme, args=("transport",),
                         key="theme_transport", use_container_width=True)
            _render_gateway_panel()


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


@st.cache_data
def district_l3():
    """전국 자치구별 L3 세부사업(build_district_l3.py). {"시도|자치구": {분야:[세부사업..]}}."""
    p = POC / "district_l3.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@st.cache_data
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


@st.cache_data
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


@st.cache_data
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


@st.cache_data
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
    """인천 10구 환경/교통 4트랙 사슬 비교 대시보드(지역현안 특화 킬러 뷰)."""
    sido, theme = _sido(), _theme()
    if sido != "인천광역시" or theme not in _THEME_MAP:
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

    recs = []
    for gu, slug in _INCHEON_GU:
        d = _domain_status_slug(slug)
        f = next((x for x in d.get("분야", []) if x["분야"] == fld), None)
        if f:
            recs.append({**f, "구": gu, "slug": slug})
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
        dot = SIG.get(row["신호"] if row else _sig(rate), "#9AA5B1")
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
    dot = SIG.get(row["신호"] if row else (_sig(fld.get("집행률", 0)) if fld else "amber"), "#9AA5B1")
    st.markdown(f"#### <span style='color:{dot}'>●</span> {dom} — 정책 이행 현황",
                unsafe_allow_html=True)
    if fld:
        if _is_full_chain(sido, gu) and row and "의안수" in row:
            k = st.columns(5)
            k[0].metric("관련 공약", f"{row.get('공약수', 0)}건")
            k[1].metric("제정 조례", f"{row['조례수']}건")
            k[2].metric("발의 의안", f"{row['의안수']}건")
            k[3].metric("분야 예산", f"{fld['예산_억']:,.0f}억")
            k[4].metric("집행률", f"{fld['집행률']:.0f}%")
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
            k[3].metric("집행률", f"{fld['집행률']:.0f}%")

    # 3개년 예산 추이 병기: 2024 결산 → 2025 결산 → 2026 편성
    b24 = _b2024_field(sido, gu, dom) if fld else None
    b26 = _b2026_field(sido, gu, dom) if fld else None
    if fld and (b24 or b26 is not None):
        parts = []
        if b24:
            parts.append(f"<b>2024 결산</b> {b24['예산_억']:,.0f}억"
                         f"<span style='color:#9AA5B1'> 집행 {b24['집행률']:.0f}%</span>")
        parts.append(f"<b>2025 결산</b> {fld['예산_억']:,.0f}억"
                     f"<span style='color:#9AA5B1'> 집행 {fld['집행률']:.0f}%</span>")
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
        _render_budget_crosscheck(sido, gu, row)
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


def _neighborhood_search(sido, gu):
    """우리 동네 예산 찾기 — 세부사업명에 지역명이 포함된 사업 검색.
    읍면동 주소 필드는 지방재정 공개 최소단위(자치구) 한계로 부재 → 사업명 텍스트 매칭."""
    key = f"{sido}|{_parent_gu(gu)}"
    biz = [b for rows in district_l3().get(key, {}).values() for b in rows]
    if not biz:
        return
    with st.expander("🔍 우리 동네 예산 찾기 — 내 세금이 어디 쓰이는지"):
        q = st.text_input("동·지역 이름 입력 (예: 검단, 청라, 가정)", key=f"nb_{key}").strip()
        if q:
            hits = [b for b in biz if q in (b.get("세부사업") or "")]
            if hits:
                tot = sum(b.get("예산_억", 0) for b in hits)
                st.caption(f"'{q}' 관련 사업 {len(hits)}건 · 예산 합계 {tot:,.0f}억원")
                for b in sorted(hits, key=lambda x: -x.get("예산_억", 0))[:12]:
                    st.markdown(f"- {b['세부사업']} <span style='color:#9AA5B1;font-size:12px'>"
                                f"(예산 {b.get('예산_억',0):,.0f}억 · 집행률 {b.get('집행률',0):.0f}%)</span>",
                                unsafe_allow_html=True)
            else:
                st.caption(f"'{q}'가 사업명에 포함된 사업이 없습니다.")
        st.caption("ⓘ 지방재정 공개 최소단위가 자치구라, 사업명에 지역명이 들어간 사업만 검색됩니다. "
                   "읍면동 단위 예산·내 동네 알림 구독은 향후 확장 예정입니다.")


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
    fields = domain_status()["분야"] if is_gn else rec["l2"]
    _field_chart(fields, "분야", f"l2_{sido}_{disp}", click=go_dom)
    _neighborhood_search(sido, gu)


def run(mode):
    label = "A안 · 동·읍 경계 표시형" if mode == "A" else "B안 · 데이터 집중형"
    st.set_page_config(page_title=f"모정 프로토타입 {mode}안", page_icon="🏛️",
                       layout="wide", initial_sidebar_state="collapsed")
    # 뒤로가기 = Streamlit 1.58 네이티브 query_params 처리(자동 rerun)에 위임.
    # 과거 popstate→location.reload()는 전체 페이지 새로고침이라 느린 환경(Cloud 무료플랜)에서
    # 뒤로가기 반응이 늦어 '두 번 눌러야' 체감을 유발 → 제거(2026-06-08, back 전수검증 PASS).
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
    if not _sido():
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
