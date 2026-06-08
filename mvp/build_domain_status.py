# -*- coding: utf-8 -*-
"""분야별 정책 이행 신호등 데이터 빌더 (#3 분야분류 + #1 이행현황).
강남 실데이터로: 공약·조례·의안을 14개 분야로 분류 → 예산·집행과 결합 →
분야별 '이행 신호등'(공약/입법/예산/집행) + 규칙기반 AI 갭·제언 생성.
산출: poc_output/domain_status_gangnam.json
"""
import json
import re
from pathlib import Path
from collections import defaultdict

POC = Path(__file__).resolve().parent.parent / "poc_output"

# 14개 재정 분야 ↔ 대표 키워드(조례·의안 분류용). 교통·환경 우선(특별상 전략).
DOMAIN_KW = {
    "교통및물류": ["교통", "도로", "주차", "버스", "지하철", "자전거", "보행", "횡단보도",
                "신호", "물류", "운수", "터널", "교량", "이면도로", "택시", "대중교통"],
    "환경": ["환경", "대기", "미세먼지", "폐기물", "재활용", "하천", "수질", "탄소",
            "기후", "녹지", "공원", "생태", "오염", "정화", "청소", "분리배출", "에너지절약"],
    "사회복지": ["복지", "아동", "노인", "경로당", "장애", "보육", "돌봄", "기초생활",
              "한부모", "가족", "여성", "청소년", "보훈", "출산", "양육", "저소득", "취약"],
    "보건": ["보건", "건강", "의료", "감염병", "정신건강", "위생", "방역", "보건소"],
    "문화및관광": ["문화", "예술", "관광", "축제", "도서관", "체육", "박물관", "공연", "관광"],
    "국토및지역개발": ["도시", "재개발", "재건축", "주택", "건축", "정비", "토지", "도시계획", "주거"],
    "일반공공행정": ["행정", "청사", "조직", "인사", "세무", "민원", "자치", "위원회", "규제"],
    "공공질서및안전": ["안전", "소방", "방범", "CCTV", "재난", "치안", "방재", "안심"],
    "산업ㆍ중소기업및에너지": ["산업", "기업", "소상공인", "창업", "일자리", "에너지", "시장", "상권", "경제"],
    "농림해양수산": ["농업", "임업", "수산", "해양", "도시농업", "농수산"],
    "교육": ["교육", "학교", "평생교육", "장학", "학습"],
    "과학기술": ["과학", "기술", "정보화", "데이터", "인공지능", "스마트", "디지털"],
}
TOK = re.compile(r"[가-힣A-Za-z]{2,}")


def classify(name):
    """이름(조례명/법안명) → 분야. 키워드 최다 매칭, 없으면 기타."""
    best, score = "기타", 0
    low = name
    for dom, kws in DOMAIN_KW.items():
        s = sum(1 for k in kws if k in low)
        if s > score:
            best, score = dom, s
    return best


def _load(n):
    return json.loads((POC / n).read_text(encoding="utf-8"))


def main():
    budget = _load("track_budget_gangnam_detail.json")
    ords = _load("track_ordinances_gangnam.json")
    council = _load("track_council_bills_gangnam.json")  # 지방의회 의안(CLIK) = ②입법 정본
    nabills = _load("track_bills_gangnam.json")           # 국회의원 법안 = 중앙입법(보조)
    pm = _load("pledge_matches.json")

    # 예산 분야별 집계
    bud = defaultdict(lambda: {"예산_억": 0.0, "집행_억": 0.0, "사업수": 0})
    for b in budget:
        d = (b.get("분야") or "기타").strip()
        bud[d]["예산_억"] += b.get("예산_억", 0)
        bud[d]["집행_억"] += b.get("집행_억", 0)
        bud[d]["사업수"] += 1

    # 조례 분야 분류
    ord_by = defaultdict(list)
    for o in ords:
        nm = o.get("표시명") or o.get("조례명", "")
        ord_by[classify(nm)].append({"조례명": nm, "공포일자": o.get("공포일자", ""),
                                     "제개정": o.get("제개정구분", "")})
    # 지방의회 의안 분야 분류 (CLIK) — ②입법 정본. 발의주체(단체장/의원) 보존
    council_by = defaultdict(list)
    for b in council:
        nm = b.get("법안명", "")
        council_by[classify(nm)].append({
            "법안명": nm, "종류": b.get("종류", ""),
            "발의주체": b.get("발의주체", ""), "대표발의": b.get("대표발의", ""),
            "발의일": b.get("발의일", ""),
        })
    # 국회의원 법안 분야 분류 — 중앙입법(보조)
    nabill_by = defaultdict(list)
    for b in nabills:
        nm = b.get("법안명", "")
        nabill_by[classify(nm)].append({"법안명": nm, "처리상태": b.get("처리상태", ""),
                                        "발의자": b.get("발의자", "")})
    # 공약: 매칭된 예산 분야로 태깅
    pledge_by = defaultdict(list)
    for p in pm.get("공약매칭", []):
        doms = {m.get("분야") for m in p.get("예산매칭", []) if m.get("분야")}
        for d in doms:
            pledge_by[d].append(p.get("공약제목", ""))

    domains = sorted(set(bud) | set(ord_by) | set(council_by),
                     key=lambda d: -bud.get(d, {}).get("집행_억", 0))

    def _ymd(s):  # '20240530'/'202205' 등 → 정수(정렬용)
        return int(re.sub(r"\D", "", str(s) or "0") or 0)

    out = []
    for d in domains:
        b = bud.get(d, {"예산_억": 0, "집행_억": 0, "사업수": 0})
        rate = round(b["집행_억"] / b["예산_억"] * 100, 1) if b["예산_억"] else 0
        n_ord = len(ord_by.get(d, []))
        c_list = council_by.get(d, [])
        n_bill = len(c_list)                      # 의안수 = 지방의회 의안(CLIK)
        n_nabill = len(nabill_by.get(d, []))      # 중앙(국회) 의안
        n_pledge = len(set(pledge_by.get(d, [])))
        recent = sorted(ord_by.get(d, []), key=lambda x: -int(x["공포일자"] or 0))[:3]
        # 최근 발의 의안 top3(발의일 내림차순) — 발의주체·대표발의 포함
        recent_bill = sorted(c_list, key=lambda x: -_ymd(x.get("발의일")))[:3]
        # 발의주체 비율(집행부 주도 vs 의회 주도 인사이트)
        n_head = sum(1 for x in c_list if x.get("발의주체") == "단체장")
        n_mem = sum(1 for x in c_list if x.get("발의주체") == "의원")

        # 규칙기반 AI 갭·제언 (분야명·수치 삽입형 구체 템플릿)
        gaps, recs = [], []
        unexec = b["예산_억"] - b["집행_억"]
        if n_pledge and n_ord == 0:
            gaps.append(f"'{d}' 분야에 관련 공약 {n_pledge}건이 있으나 이를 뒷받침할 조례가 "
                        f"확인되지 않아 입법 공백이 우려됩니다.")
            recs.append(f"공약 이행 근거 마련을 위해 '{d}' 분야 조례 제정·정비 검토가 필요합니다.")
        if rate and rate < 70:
            gaps.append(f"'{d}' 예산 {b['예산_억']:,.0f}억원 대비 집행률이 {rate}%에 그쳐, "
                        f"미집행 약 {unexec:,.0f}억원의 사유 점검이 필요합니다.")
            recs.append(f"{b['사업수']}개 세부사업의 집행 지연 사유를 확인하고 잔여 예산 집행을 독려할 필요가 있습니다.")
        if n_ord and recent and int(recent[0]["공포일자"] or 0) >= 20240000:
            recs.append(f"최근 '{recent[0]['조례명']}' 등 입법이 활발합니다 — "
                        f"후속 예산 편성·집행 연계를 모니터링하세요.")
        if rate >= 90 and n_ord and not gaps:
            recs.append(f"집행률 {rate}%로 양호합니다 — 집행 성과·시민 만족도를 공개해 신뢰를 강화하세요.")
        if not gaps:
            gaps.append(f"'{d}' 분야는 조례 {n_ord}건·예산 {b['예산_억']:,.0f}억·집행률 {rate}%로 "
                        f"공약→입법→예산→집행이 비교적 연결되어 있습니다(추가 점검 권장).")
        if not recs:
            recs.append("현 수준을 유지하고 집행 성과 공개로 신뢰를 강화하세요.")

        # 신호등 상태(간이): 집행률·입법 유무로 색
        status = "green" if (rate >= 80 and n_ord) else ("amber" if (rate >= 50 or n_ord) else "red")

        out.append({
            "분야": d,
            "공약수": n_pledge,
            "조례수": n_ord,
            "의안수": n_bill,            # 지방의회 의안(CLIK)
            "중앙의안수": n_nabill,       # 국회의원 법안(보조)
            "의안단체장": n_head,
            "의안의원": n_mem,
            "예산_억": round(b["예산_억"], 1),
            "집행_억": round(b["집행_억"], 1),
            "집행률": rate,
            "사업수": b["사업수"],
            "최근조례": recent,
            "최근의안": recent_bill,
            "신호": status,
            "남은과제": gaps,
            "AI제언": recs,
        })

    # 발의주체 전체 비율(집행부 vs 의회 주도)
    tot_head = sum(1 for b in council if b.get("발의주체") == "단체장")
    tot_mem = sum(1 for b in council if b.get("발의주체") == "의원")
    meta = {"지역": "서울특별시 강남구",
            "기준": "2023 결산 + law.go.kr 조례 + CLIK 강남구의회 의안",
            "조례총": len(ords), "의안총": len(council), "중앙의안총": len(nabills),
            "의안단체장총": tot_head, "의안의원총": tot_mem}
    (POC / "domain_status_gangnam.json").write_text(
        json.dumps({"메타": meta, "분야": out}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print(f"분야 {len(out)}개 · 조례 {len(ords)} · 지방의안 {len(council)}(단체장 {tot_head}/의원 {tot_mem}) · 국회법안 {len(nabills)}")
    for r in out[:8]:
        print(f"  [{r['신호']}] {r['분야']:18s} 공약{r['공약수']} 조례{r['조례수']:2d} "
              f"의안{r['의안수']:2d} 예산{r['예산_억']:>8,.0f}억 집행률{r['집행률']:.0f}%")


if __name__ == "__main__":
    main()
