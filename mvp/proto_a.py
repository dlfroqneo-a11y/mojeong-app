# 모정(茅亭) — 서비스 이전 안내 (2026-07-08)
# 이 Streamlit Cloud 버전은 은퇴했습니다. 정식 서비스는 AWS 자체 호스팅(Next.js)으로 이전되었으며,
# 모든 신청서·공식 고지는 아래 AWS 주소를 사용합니다. 방문 시 자동으로 이동합니다.
import streamlit as st
import streamlit.components.v1 as components

AWS_URL = "http://52.192.132.66/"

st.set_page_config(page_title="모정 — 새 주소로 이전", page_icon="🏛️", layout="centered")

# 부모 창(앱 페이지)을 AWS로 자동 리다이렉트
components.html(f'<script>window.parent.location.href = "{AWS_URL}";</script>', height=0)

st.title("모정 茅亭 — 정책 데이터 체인")
st.subheader("이 서비스는 새 주소로 이전되었습니다")
st.markdown(f"## 👉 [{AWS_URL}]({AWS_URL})")
st.info("이전 Streamlit 버전은 더 이상 사용하지 않습니다(AWS 자체 호스팅 정식판으로 대체). 자동으로 이동하지 않으면 위 링크를 눌러 주세요.")
