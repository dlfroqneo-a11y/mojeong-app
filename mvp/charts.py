# -*- coding: utf-8 -*-
"""
모정 MVP 0.5 — Plotly 인터랙티브 차트 모듈
PoC v3·v4 의 정적 matplotlib 차트를 인터랙티브로 전환.
"""
import plotly.graph_objects as go
import plotly.express as px

# ---- 브랜드 컬러 팔레트 ----------------------------------------------------
PRIMARY = "#2C5F6F"   # 깊은 청록 (茅亭 — 정자의 차분함)
ACCENT = "#E8743B"    # 강조 주황 (선택 자치구)
WARN = "#D64545"      # 경고 빨강 (감시 대상)
GOOD = "#3D9970"      # 양호 초록
NEUTRAL = "#9AA5B1"   # 회색 (전국 평균/기타)
GRID = "#E4E7EB"

FONT = dict(family="Malgun Gothic, AppleGothic, sans-serif", color="#323F4B")


def _base_layout(fig, height=380, title=None):
    fig.update_layout(
        height=height,
        title=dict(text=title, font=dict(size=16, **FONT)) if title else None,
        font=FONT,
        paper_bgcolor="white",
        plot_bgcolor="white",
        margin=dict(l=20, r=20, t=50 if title else 30, b=30),
        hoverlabel=dict(font=dict(family="Malgun Gothic, sans-serif")),
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID)
    return fig


def bar_25_districts(df, metric_key, label, unit, selected=None):
    """25개 자치구 비교 막대 — 선택 자치구는 ACCENT 로 강조."""
    d = df.sort_values(metric_key, ascending=True)
    colors = [
        ACCENT if name == selected else PRIMARY
        for name in d["district"]
    ]
    fig = go.Figure(go.Bar(
        x=d[metric_key],
        y=d["district"],
        orientation="h",
        marker_color=colors,
        text=[f"{v:.1f}" for v in d[metric_key]],
        textposition="outside",
        hovertemplate="%{y}<br>" + label + ": %{x:.2f} " + unit + "<extra></extra>",
    ))
    avg = d[metric_key].mean()
    fig.add_vline(x=avg, line_dash="dash", line_color=NEUTRAL,
                  annotation_text=f"평균 {avg:.1f}", annotation_position="top")
    _base_layout(fig, height=620, title=f"25개 자치구 · {label} ({unit})")
    fig.update_layout(margin=dict(l=20, r=60, t=50, b=30))
    return fig


def timeseries_line(series, label, unit, conv, region_name="강남구"):
    """5년 시계열 — 선택 지역 vs 전국 평균."""
    years = [r["year"] for r in series]
    local = [conv(r["gangnam"]) for r in series]
    natl = [conv(r["national_avg"]) for r in series]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=years, y=natl, name="전국 평균", mode="lines+markers",
        line=dict(color=NEUTRAL, width=2, dash="dot"),
        marker=dict(size=7),
        hovertemplate="전국 평균<br>%{x}: %{y:.1f} " + unit + "<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=years, y=local, name=region_name, mode="lines+markers",
        line=dict(color=ACCENT, width=3),
        marker=dict(size=9),
        fill="tonexty", fillcolor="rgba(232,116,59,0.08)",
        hovertemplate=region_name + "<br>%{x}: %{y:.1f} " + unit + "<extra></extra>",
    ))
    _base_layout(fig, height=340, title=f"{label} 5년 추이 ({unit})")
    fig.update_layout(legend=dict(orientation="h", y=1.12, x=0))
    return fig


def radar_5_metrics(df, focus_names):
    """핵심 자치구 5종 스파이더 — 4지표 0~100 정규화."""
    metrics = ["수의계약비율", "업무추진비", "지방의회경비", "행사축제경비비율"]
    labels = ["수의계약", "업무추진비", "지방의회경비", "행사축제경비"]
    # 0~100 정규화 (지표별 max 기준)
    norm = df.copy()
    for m in metrics:
        mx = df[m].max() or 1
        norm[m] = df[m] / mx * 100
    palette = [ACCENT, PRIMARY, GOOD, WARN, NEUTRAL]
    fig = go.Figure()
    for i, name in enumerate(focus_names):
        row = norm[norm["district"] == name]
        if row.empty:
            continue
        vals = [row.iloc[0][m] for m in metrics]
        fig.add_trace(go.Scatterpolar(
            r=vals + [vals[0]],
            theta=labels + [labels[0]],
            name=name,
            line=dict(color=palette[i % len(palette)], width=2),
            fill="toself", opacity=0.55,
        ))
    fig.update_layout(
        height=440, font=FONT, paper_bgcolor="white",
        polar=dict(radialaxis=dict(visible=True, range=[0, 100], gridcolor=GRID)),
        legend=dict(orientation="h", y=-0.05),
        margin=dict(l=40, r=40, t=40, b=40),
        title=dict(text="핵심 자치구 4지표 비교 (지표별 최대=100 정규화)",
                   font=dict(size=15, **FONT)),
    )
    return fig


def bias_bar(metrics_dict, df):
    """선택 자치구의 4지표 — 25구 평균 대비 편차(%)."""
    rows = []
    for key, label, unit, _ in [
        ("수의계약비율", "수의계약 비율", "%", ""),
        ("업무추진비", "업무추진비", "백만원", ""),
        ("지방의회경비", "지방의회 경비", "백만원", ""),
        ("행사축제경비비율", "행사·축제 경비", "%", ""),
    ]:
        avg = df[key].mean()
        if avg == 0:
            continue
        bias = (metrics_dict[key] - avg) / avg * 100
        rows.append((label, bias))
    rows.sort(key=lambda x: x[1])
    labels = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    colors = [WARN if v > 0 else GOOD for v in vals]
    fig = go.Figure(go.Bar(
        x=vals, y=labels, orientation="h", marker_color=colors,
        text=[f"{v:+.0f}%" for v in vals], textposition="outside",
        hovertemplate="%{y}<br>전국 대비 %{x:+.1f}%<extra></extra>",
    ))
    fig.add_vline(x=0, line_color=NEUTRAL)
    _base_layout(fig, height=300, title="전국(25구) 평균 대비 편차")
    fig.update_layout(margin=dict(l=20, r=60, t=50, b=30))
    return fig


def field_execution(field_list):
    """③예산 분야별 집행률 막대 (집행/예산)."""
    rows = []
    for f in field_list:
        ep, cpl = f.get("ep", 0), f.get("cpl", 0)
        if cpl:
            rows.append((f["분야"], ep / cpl * 100, ep / 1e8, cpl / 1e8))
    rows.sort(key=lambda x: -x[3])
    rows = rows[:8]
    names = [r[0] for r in rows]
    rates = [r[1] for r in rows]
    colors = [GOOD if r >= 80 else (ACCENT if r >= 50 else WARN) for r in rates]
    fig = go.Figure(go.Bar(
        x=rates, y=names, orientation="h", marker_color=colors,
        text=[f"{r:.0f}%" for r in rates], textposition="outside",
        customdata=[(r[2], r[3]) for r in rows],
        hovertemplate="%{y}<br>집행률 %{x:.1f}%<br>집행 %{customdata[0]:,.0f}억 / 예산 %{customdata[1]:,.0f}억<extra></extra>",
    ))
    fig.add_vline(x=80, line_dash="dash", line_color=NEUTRAL,
                  annotation_text="80%", annotation_position="top")
    _base_layout(fig, height=380, title="분야별 집행률 (예산 규모 상위 8)")
    fig.update_layout(margin=dict(l=20, r=60, t=50, b=30), xaxis_range=[0, 115])
    return fig


def execution_timeseries(ts):
    """④집행 5년 시계열 (총수입, 억원)."""
    years = [r["연도"] for r in ts]
    vals = [r["총수입"] for r in ts]
    fig = go.Figure(go.Scatter(
        x=years, y=vals, mode="lines+markers",
        line=dict(color=PRIMARY, width=3), marker=dict(size=10),
        fill="tozeroy", fillcolor="rgba(44,95,111,0.10)",
        text=[f"{v:.0f}" for v in vals], textposition="top center",
        hovertemplate="%{x}년<br>%{y:.1f}억<extra></extra>",
    ))
    _base_layout(fig, height=320, title="집행(결산) 5년 추이 (억원)")
    return fig
