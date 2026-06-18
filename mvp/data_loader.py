# -*- coding: utf-8 -*-
"""
모정 MVP 0.5 — 데이터 로더
PoC v1~v4 가 생성한 poc_output/*.json 을 읽어 Streamlit 앱에 공급한다.
정부 API 직접 호출 금지 원칙(설계문서 Part1 §1)에 따라, 앱은 자체 캐시(JSON)와만 통신한다.
"""
import json
import os
from functools import lru_cache

# poc_output 절대 경로 (mvp/ 기준 상위 폴더). MOJEONG_POC env로 오버라이드 가능(8505 미래버전).
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POC_DIR = os.environ.get("MOJEONG_POC") or os.path.join(_BASE, "poc_output")


def _load(name):
    path = os.path.join(POC_DIR, name)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_all():
    """모든 PoC 산출 데이터를 한 번에 로드(캐시)."""
    return {
        "districts": _load("poc_v4_multi_district.json"),   # 25개 자치구 4지표
        "insights": _load("poc_v3_insights.json"),          # 강남구 5년 시계열 + 인사이트
        "integrated": _load("poc_v1_5_integrated.json"),    # 강남구 4트랙
    }


# ---- 자치구 목록 / 메타 ----------------------------------------------------

def district_list():
    """25개 자치구 이름 리스트(수의계약비율 내림차순)."""
    d = load_all()["districts"]["districts_25"]
    return [x["district"] for x in sorted(d, key=lambda r: -r["수의계약비율"])]


def district_metrics(name):
    """선택 자치구의 4지표 dict 반환(없으면 None)."""
    for x in load_all()["districts"]["districts_25"]:
        if x["district"] == name:
            return x
    return None


# 풀 데이터(4트랙 + 5년 시계열)를 보유한 대표 자치구
FULL_DATA_DISTRICT = "강남구"


def has_full_data(name):
    return name == FULL_DATA_DISTRICT


# ---- 25구 비교용 데이터프레임 ---------------------------------------------

def districts_dataframe():
    import pandas as pd
    return pd.DataFrame(load_all()["districts"]["districts_25"])


# 4지표 메타: (키, 라벨, 단위, 방향) — 방향 'bad'=높을수록 감시대상
# 주의: 업무추진비/지방의회경비는 v4 데이터에서 '억원' 단위(v3 시계열 원 단위 ÷1e8 과 일치)
METRIC_META = [
    ("수의계약비율", "수의계약 비율", "%", "bad"),
    ("업무추진비", "업무추진비", "억원", "bad"),
    ("지방의회경비", "지방의회 경비", "억원", "bad"),
    ("행사축제경비비율", "행사·축제 경비 비율", "%", "neutral"),
]


# ---- 강남구 5년 시계열 -----------------------------------------------------

def timeseries(metric_key):
    """
    poc_v3_insights 시계열 지표 반환.
    metric_key: '재정자립도[최종]' | '수의계약비율' | '행사축제경비비율'
                | '지방의회경비비율' | '업무추진비비율'
    -> [{year, gangnam, national_avg}, ...]
    """
    return load_all()["insights"]["시계열_지표"].get(metric_key, [])


def timeseries_metric_keys():
    return list(load_all()["insights"]["시계열_지표"].keys())


# 시계열 지표 표시 메타: (키, 라벨, 단위, 변환함수)
def _to_eok(v):      # 원 -> 억원
    return v / 1e8
def _to_baekman(v):  # 원 -> 백만원
    return v / 1e6

TS_META = {
    "재정자립도[최종]": ("재정 규모(자체수입)", "억원", _to_eok),
    "수의계약비율": ("수의계약 비율", "%", lambda v: v),
    "행사축제경비비율": ("행사·축제 경비 비율", "%", lambda v: v),
    "지방의회경비비율": ("지방의회 경비", "억원", _to_eok),
    "업무추진비비율": ("업무추진비", "억원", _to_eok),
}


# ---- AI 인사이트 -----------------------------------------------------------

def insights():
    return load_all()["insights"]["인사이트"]


def insight_region():
    ins = load_all()["insights"]
    return ins.get("지역", ""), ins.get("기간", "")


# ---- 공약 → 예산/입법 연결 매칭 (MVP 차별점, match_pledges.py 산출) --------

@lru_cache(maxsize=1)
def pledge_matches():
    """match_pledges.py 가 생성한 공약↔예산/법안 매칭 결과.
    {"메타": {...}, "공약매칭": [{공약제목, 핵심키워드, 예산매칭[], 법안매칭[]}...]}"""
    try:
        return _load("pledge_matches.json")
    except FileNotFoundError:
        return {"메타": {}, "공약매칭": []}


def has_pledge_matches():
    return bool(pledge_matches().get("공약매칭"))


# ---- 4트랙(강남구) ---------------------------------------------------------

def tracks():
    return load_all()["integrated"]["트랙"]


def integrated_meta():
    d = load_all()["integrated"]
    return d.get("지역", ""), d.get("기준일", "")


# ---- 지도 Drill-Down (L1→L2→L3) ------------------------------------------
# 전국 17개 시도 L1·L2 = sido_finance.json (2023 결산, QWGJK).
# L3 세부사업 = 서울(강남구 PoC)만 시연, 나머지는 MVP 1.0 확장 예정.
_SIDO_FIN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "data", "sido_finance.json")
L3_SIDO = "서울특별시"   # L3 세부사업 보유(강남구 PoC)


@lru_cache(maxsize=1)
def load_sido_finance():
    with open(_SIDO_FIN_PATH, encoding="utf-8") as f:
        return json.load(f)


def drill_sidos():
    """L1·L2 데이터를 보유한 시도 목록(현재 17개 전부)."""
    return list(load_sido_finance().keys())


def has_drill_data(sido):
    return sido in load_sido_finance()


def l1_summary(sido):
    """L1 총괄 — 선택 시도 재정 한눈 요약(2023 결산). 서울만 5년 집행 추이."""
    fin = load_sido_finance().get(sido, {})
    l1 = fin.get("l1", {})
    # L1·L2·L3 모두 시도 본청 2023 결산(QWGJK)으로 전국 일관. 강남 PoC 는
    # 공약·입법·시민감시·AI 트랙에만 해당하므로 재정 라벨에는 붙이지 않는다.
    ts = tracks().get("④집행_시계열", []) if sido == L3_SIDO else []
    label = sido
    return {
        "region": label,
        "기준": "2023 회계연도 결산",
        "총예산_억": l1.get("총예산_억", 0),
        "총집행_억": l1.get("총집행_억", 0),
        "집행률": l1.get("집행률", 0),
        "총사업수": l1.get("사업수", 0),
        "분야수": l1.get("분야수", 0),
        "집행추이": ts,
    }


def l2_fields(sido):
    """L2 분야별 — 세출 분야(집행/예산/집행률), 집행 규모 내림차순."""
    l2 = load_sido_finance().get(sido, {}).get("l2", [])
    return sorted(l2, key=lambda x: -x.get("집행_억", 0))


def l3_programs(sido, field_name):
    """L3 세부사업 — 선택 분야의 세부사업 리스트.
    sido_finance 에 l3 가 있으면 사용(전국 확장 시), 없으면 서울만 강남 상위10 필터."""
    # 1) sido_finance 에 l3 가 채워져 있으면 우선 사용
    l3 = load_sido_finance().get(sido, {}).get("l3", {})
    if l3 and field_name in l3:
        return sorted(l3[field_name], key=lambda x: -x.get("집행_억", 0))
    # 2) fallback: 서울 = 강남구 예산 상위10 필터
    if sido != L3_SIDO:
        return []
    top = tracks().get("③예산_상위10", [])
    out = []
    for p in top:
        if p.get("분야") == field_name:
            ex, bd = p.get("집행", 0), p.get("예산", 0)
            out.append({
                "사업명": p.get("사업명", ""),
                "집행_억": ex / 1e8,
                "예산_억": bd / 1e8,
                "집행률": (ex / bd * 100) if bd else 0,
            })
    return sorted(out, key=lambda x: -x["집행_억"])
