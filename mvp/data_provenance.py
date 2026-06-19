# -*- coding: utf-8 -*-
"""모정 데이터 출처·신뢰등급(A/B/C) 레지스트리 — 다중소스 교차검증 투명성 레이어.

팀장 원칙(2026-06-04): "모든 부분이 한·두 소스가 아니라 여러 소스로 교차검증".
Gemini 자문(2026-06-04): 과대포장 대신 provenance(출처·등급) 투명 공개가 신뢰를 얻는다.
  - A = 정부 공식 API 직접 연계
  - B = 공식문서 가공/언론/NGO (출처URL 아웃링크, 본문 비저장 = 저작권 방어)
  - C = 시스템 추정 매핑(NLP 유사도) — '추정'임을 명시
"""

# 등급 → (라벨, 색)
GRADE = {
    "A": ("정부 공식 API", "#2E7D5B"),      # green
    "B": ("공식문서·언론·NGO", "#C77A1B"),   # amber
    "C": ("시스템 추정 매핑", "#8A94A6"),     # gray
}


def badge(grade, label=None):
    """A/B/C 등급 인라인 배지 HTML."""
    g = (grade or "").split("/")[0].strip()
    name, color = GRADE.get(g, ("미상", "#9AA5B1"))
    txt = label or f"{grade}등급"
    return (f"<span style='display:inline-block;background:{color};color:#fff;"
            f"font-size:10px;font-weight:700;padding:1px 6px;border-radius:8px;"
            f"vertical-align:middle' title='{name}'>{txt}</span>")


# 4트랙 소스 명세 (교차검증 유형 = 보강/일치검증/병치, 상태 = 실연계/신청대기/예정)
TRACK_SOURCES = [
    {"트랙": "① 공약", "등급": "A/B", "상태": "✅ 실연계",
     "1차": "중앙선관위 선거공약정보(15040587)",
     "2차": "지자체 공약이행평가·검증 언론(선관위 미등록 단체장 보강, 출처URL 명시)",
     "갱신": "선거주기·이행평가 시", "교차검증": "보강 + 병치"},
    {"트랙": "② 의안", "등급": "A", "상태": "✅ 실연계",
     "1차": "국회도서관 지방의정포털 CLIK 의안(발의)",
     "2차": "CLIK 회의록(minutes·심의) — 발의 의안↔심의 활동 보강(동일 RASMBLY_ID)",
     "갱신": "회기별", "교차검증": "보강(CLIK 2소스)"},
    {"트랙": "② 조례", "등급": "A", "상태": "✅ 실연계",
     "1차": "국가법령정보 law.go.kr 공포조례(법제처)",
     "2차": "CLIK 조례안(지방의정포털·행안부) — 발의↔공포 입법사슬 일치검증",
     "갱신": "수시(공포 시)", "교차검증": "일치성 검증(독립 2기관)"},
    {"트랙": "③ 예산", "등급": "A", "상태": "✅ 실연계",
     "1차": "지방재정365 lofin365 세출현황 — 편성 예산서(cpl)",
     "2차": "동 lofin365 집행 결산(ep) — 사전 편성 vs 사후 결산 2단계 병치 + 이상치(미집행·추경) 검출. "
            "※열린재정(기재부)·ELIS는 각각 국가레벨·API부재로 자치구 직접 병치 부적합 확인(2026-06-04)",
     "갱신": "분기", "교차검증": "병치(편성↔결산)"},
    {"트랙": "④ 집행", "등급": "A", "상태": "✅ 실연계",
     "1차": "지방재정365 집행액(ep_amt)",
     "2차": "★조달청 나라장터 계약실적(getDataSetOpnStdCntrctInfo) — 예산서 통계 vs 실제 계약(업체·금액·계약방법) 병치. 수의계약 비율 등 집행 투명성 검증",
     "갱신": "수시(계약체결 시)", "교차검증": "병치(차별점·집행 실체)"},
]

# 지도(행정구역 경계) 데이터 출처 — A/B 등급
MAP_SOURCES = [
    {"항목": "시군구 경계", "등급": "B",
     "출처": "통계청 SGIS / 행정안전부 도로명주소 행정구역 경계(메르카토르 투영·자치구 단위 정규화)",
     "갱신": "행정개편 시"},
    {"항목": "행정동(읍·면·동) 경계", "등급": "B",
     "출처": "행정안전부 도로명주소 행정동경계(월 1회 갱신) — admdongkor 정리본(ver2026.04 기준)",
     "갱신": "월 1회"},
    {"항목": "인천 2026.7.1 개편 지도", "등급": "B",
     "출처": "「인천광역시 제물포구·영종구 및 검단구 설치 등에 관한 법률」(2024-01-30 공포) 근거 + 도로명주소 행정동경계로 영종/제물포/서해/검단 재구성",
     "갱신": "7.1 정부 정형 데이터 등재 시 교차검증 후 교체"},
]

# 매칭(연결) 정직 고지 — Gemini 리스크 #1(유니크키 부재) 방어
MATCH_DISCLOSURE = (
    "트랙 간 연결(공약↔조례↔예산↔의안)은 한국 공공데이터에 공통 식별키가 없어, "
    "사업명·조례명·의안명의 형태소 분석 + 한국어 TF-IDF 유사도로 1차 매핑하고 "
    "대표 공약은 수동 보정(Human-in-the-loop)했습니다. 향후 LLM 기반 자동매핑으로 고도화 예정."
)

# 시점 불일치 고지 — Gemini 리스크 #2
TIMELAG_NOTICE = (
    "예산·집행은 확정 결산(분기) 기준, 조례·의안은 수시 갱신이라 트랙 간 시점이 다를 수 있습니다."
)

# 지역 상징마크(CI)/휘장 표시 — 명목적 사용 면책 고지
SYMBOL_DISCLOSURE = (
    "지도에서 각 지역에 마우스를 올리면 좌측 지역명 위에 해당 지역의 상징마크(CI)·휘장이 함께 표시됩니다. "
    "이는 각 지역을 식별·안내하기 위한 목적(명목적 사용)으로만 사용한 것이며, "
    "각 상징물의 저작권·상표권은 해당 지방자치단체에 있습니다. "
    "본 서비스는 해당 기관과 무관하며, 표시가 그 기관의 후원·공식 인증·제휴를 의미하지 않습니다. "
    "정식 서비스 전환 시에는 각 자치단체의 사용 승인을 받은 뒤 게재할 예정입니다."
)


def symbol_sources():
    """geo/symbols.json 에서 (실제 이미지 파일이 존재하는) 상징마크 출처 목록을 반환.
    반환 = [{region, source, license, url}] (표시명 기준 중복 제거)."""
    import json as _json
    import os as _os
    base = _os.path.dirname(_os.path.abspath(__file__))
    reg_path = _os.path.join(base, "geo", "symbols.json")
    sym_dir = _os.path.join(base, "geo", "symbols")
    try:
        with open(reg_path, encoding="utf-8") as f:
            reg = _json.load(f)
    except Exception:
        return []
    out, seen = [], set()
    for key, meta in (reg.get("symbols") or {}).items():
        fn = (meta or {}).get("file")
        if not fn or not _os.path.exists(_os.path.join(sym_dir, fn)):
            continue                       # 이미지 미확보(레지스트리만 존재) → 출처 표시 안 함
        src = meta.get("source", "")
        if src in seen:
            continue
        seen.add(src)
        out.append({"region": key, "source": src,
                    "license": meta.get("license", "미상"), "url": meta.get("url", "")})
    return out
