import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="사용자 행동 탐색 대시보드", layout="wide")
st.title("사용자 행동 탐색 대시보드")
st.caption("주별 세션 퍼널 | 플랫폼 · 유입 경로별 | 2025.07 ~ 2026.06")

# ------------------------------------------------------------
# 0. 필요한 데이터만 추출 — 쓸 테이블과 컬럼만 메모리 DB에 올리고, 정제 테이블 events를 처음 한 번만 만듦
# ------------------------------------------------------------
DATA_DIR = "data"                          # 원본 CSV를 넣어 둔 폴더
NEEDED = {                                 # 이번 대시보드에 필요한 테이블과 컬럼
    "app_events": ["event_id", "event_datetime", "customer_id", "session_id",
                   "event_name", "platform", "traffic_source"],
}

@st.cache_resource
def get_db():                              # 필요한 컬럼만 메모리 DB에 올리고, 정제 테이블 events를 만듦 (처음 한 번만)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    for name, cols in NEEDED.items():
        pd.read_csv(f"{DATA_DIR}/{name}.csv", usecols=cols).to_sql(name, con, index=False)
    # 5주차 제외 규칙: QA 계정 제외 · 중복 이벤트 제거
    con.execute("""
        CREATE TABLE events AS
        SELECT event_id, event_datetime, customer_id, session_id, event_name, platform, traffic_source
          FROM (SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY session_id, event_datetime, event_name
                                          ORDER BY event_id) AS dup
                  FROM app_events
                 WHERE customer_id NOT LIKE 'QA%')
         WHERE dup = 1
    """)
    # 세션별로 묶는 집계가 빨라지도록 session_id에 인덱스를 붙임 (없으면 첫 실행이 몇 분 걸릴 수 있음)
    con.execute("CREATE INDEX idx_events_session ON events(session_id)")
    return con

# ------------------------------------------------------------
# 1. 데이터 준비 — 정제된 events 테이블을 SQL로 집계
# ------------------------------------------------------------
@st.cache_data
def query(sql):                            # SQL을 실행해 표로 돌려줌
    return pd.read_sql(sql, get_db())

# 주 × 플랫폼 × 유입 경로별 세션 퍼널
SQL_WEEKLY_FUNNEL = """
WITH s AS (                                 -- 세션의 첫 이벤트로 플랫폼 · 유입 경로 · 주 정하기
    SELECT session_id, platform, traffic_source,
           DATE(MIN(event_datetime), 'weekday 0', '-6 days') AS 주시작일
      FROM events
     GROUP BY session_id
)
SELECT s.주시작일,
       s.platform       AS 플랫폼,
       s.traffic_source AS 유입경로,
       COUNT(DISTINCT e.session_id)                                                       AS 세션수,
       COUNT(DISTINCT CASE WHEN e.event_name = 'view_restaurant' THEN e.session_id END)   AS 조회세션,
       COUNT(DISTINCT CASE WHEN e.event_name = 'add_to_cart'     THEN e.session_id END)   AS 장바구니세션,
       COUNT(DISTINCT CASE WHEN e.event_name = 'begin_checkout'  THEN e.session_id END)   AS 결제시작세션,
       COUNT(DISTINCT CASE WHEN e.event_name = 'order_complete'  THEN e.session_id END)   AS 주문세션,
       SUM(CASE WHEN e.event_name = 'payment_fail' THEN 1 ELSE 0 END)                     AS 결제실패
  FROM events e
  JOIN s ON e.session_id = s.session_id
 WHERE s.주시작일 < '2026-06-29'            -- 이틀치뿐인 마지막 주 제외
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

@st.cache_data
def load_data():
    data = query(SQL_WEEKLY_FUNNEL)
    data["주시작일"] = pd.to_datetime(data["주시작일"]).dt.date
    return data

df = load_data()                           # 주 × 플랫폼 × 유입 경로별 세션 퍼널

# ------------------------------------------------------------
# 1-1. 사이드바 필터 — 기간 · 플랫폼 · 유입 경로
# ------------------------------------------------------------
st.sidebar.header("필터")

weeks = sorted(df["주시작일"].unique())
start_week, end_week = st.sidebar.select_slider(
    "기간", options=weeks, value=(weeks[0], weeks[-1]))

platforms = sorted(df["플랫폼"].unique())
sel_platforms = st.sidebar.multiselect("플랫폼", platforms, default=platforms)

sources = sorted(df["유입경로"].unique())
sel_sources = st.sidebar.multiselect("유입 경로", sources, default=sources)

f = df[df["주시작일"].between(start_week, end_week)
       & df["플랫폼"].isin(sel_platforms)
       & df["유입경로"].isin(sel_sources)]

if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 바꿔 주세요.")
    st.stop()

# ------------------------------------------------------------
# 2. KPI 카드
# ------------------------------------------------------------
def rate(a, b):
    return a / b * 100 if b else 0

t = f[["세션수", "결제시작세션", "주문세션"]].sum()
c1, c2, c3 = st.columns(3)
c1.metric("세션 수", f"{t['세션수']:,}개")
c2.metric("주문 전환율 (방문 → 주문)", f"{rate(t['주문세션'], t['세션수']):.1f}%")
c3.metric("결제 전환율 (결제 시작 → 주문)", f"{rate(t['주문세션'], t['결제시작세션']):.1f}%")

st.divider()

# ------------------------------------------------------------
# 3. 차트
# ------------------------------------------------------------
left, right = st.columns(2)

# 3-1. 주별 결제 전환율 (플랫폼별)
wp = f.groupby(["주시작일", "플랫폼"])[["결제시작세션", "주문세션"]].sum().reset_index()
wp["결제전환율"] = (wp["주문세션"] / wp["결제시작세션"] * 100).round(1)
fig1 = px.line(wp, x="주시작일", y="결제전환율", color="플랫폼", markers=True,
               title="주별 결제 전환율 (플랫폼별)")
fig1.update_layout(yaxis_title="결제 전환율 (%)", xaxis_title="")
left.plotly_chart(fig1, width="stretch")

# 3-2. 세션 퍼널
steps = {"1.방문": "세션수", "2.식당조회": "조회세션", "3.장바구니": "장바구니세션",
         "4.결제시작": "결제시작세션", "5.주문완료": "주문세션"}
fun = pd.DataFrame({"단계": list(steps), "세션수": [f[c].sum() for c in steps.values()]})
fig2 = px.funnel(fun, x="세션수", y="단계", title="세션 퍼널")
right.plotly_chart(fig2, width="stretch")

# 3-3. 유입 경로별 주문 전환율
ws = f.groupby("유입경로")[["세션수", "주문세션"]].sum().reset_index()
ws["주문전환율"] = (ws["주문세션"] / ws["세션수"] * 100).round(1)
ws = ws.sort_values("주문전환율")
fig3 = px.bar(ws, x="주문전환율", y="유입경로", orientation="h", text_auto=".1f",
              hover_data=["세션수"], title="유입 경로별 주문 전환율")
fig3.update_xaxes(range=[0, ws["주문전환율"].max() * 1.2])
fig3.update_layout(xaxis_title="주문 전환율 (%)", yaxis_title="")
st.plotly_chart(fig3, width="stretch")

# ------------------------------------------------------------
# 4. 집단 비교 — 플랫폼 또는 유입 경로별 퍼널 비교
# ------------------------------------------------------------
st.divider()
st.subheader("집단 비교")

by = st.radio("비교 기준", ["플랫폼", "유입경로"], horizontal=True)

stage_cols = ["세션수", "조회세션", "장바구니세션", "결제시작세션", "주문세션"]
stage_names = ["1.방문", "2.식당조회", "3.장바구니", "4.결제시작", "5.주문완료"]

# 집단별 퍼널 5단계 합계
grp = f.groupby(by)[stage_cols].sum().reset_index()
groups = grp[by].tolist()

# 긴 형태로 바꿔 단계별 전환율 계산
cmp = grp.melt(id_vars=by, value_vars=stage_cols, var_name="컬럼", value_name="세션")
cmp["단계"] = cmp["컬럼"].map(dict(zip(stage_cols, stage_names)))
cmp = cmp.sort_values([by, "단계"]).reset_index(drop=True)

first = cmp.groupby(by)["세션"].transform("first")          # 집단별 첫 단계(방문) 세션 수
prev = cmp.groupby(by)["세션"].shift(1)                      # 직전 단계 세션 수
cmp["누적전환율"] = (cmp["세션"] / first.where(first > 0) * 100).round(1)
cmp["기준세션"] = prev
cmp["단계전환율"] = (cmp["세션"] / prev.where(prev > 0) * 100).round(1)

step = cmp[cmp["단계"] != "1.방문"].copy()                    # 단계 전환율은 2단계부터
step["기준세션"] = step["기준세션"].astype("int64")

orders = {by: groups, "단계": stage_names}
cl, cr = st.columns(2)

# 4-1. 누적 전환율 퍼널 (첫 단계 대비 %)
fig4 = px.funnel(cmp, x="누적전환율", y="단계", color=by, category_orders=orders,
                 hover_data={"세션": ":,"}, title=f"{by}별 누적 전환율 (방문 대비 %)")
fig4.update_traces(texttemplate="%{x:.1f}%")
# 1.방문이 맨 위, 5.주문완료가 맨 아래에 오도록 단계 순서를 고정
fig4.update_yaxes(categoryorder="array", categoryarray=stage_names, autorange="reversed")
cl.plotly_chart(fig4, width="stretch")

# 4-2. 단계 전환율 막대 (직전 단계 대비 %)
fig5 = px.bar(step, x="단계", y="단계전환율", color=by, barmode="group",
              category_orders=orders, text_auto=".1f",
              hover_data={"기준세션": ":,", "세션": ":,"},
              title=f"{by}별 단계 전환율 (직전 단계 대비 %)")
fig5.update_layout(xaxis_title="", yaxis_title="단계 전환율 (%)")
cr.plotly_chart(fig5, width="stretch")

# 세션 수가 적은 집단 안내
small = grp.loc[grp["세션수"] < 500, by].tolist()
if small:
    st.caption(f"세션 수가 500개 미만인 집단({', '.join(map(str, small))})은 "
               "값이 크게 흔들릴 수 있습니다. 해석에 주의해 주세요.")

# ------------------------------------------------------------
# 5. 드릴다운 — 언제부터, 무엇 때문일까
# ------------------------------------------------------------
st.divider()
st.subheader("드릴다운: 언제부터, 무엇 때문일까")

other = "유입경로" if by == "플랫폼" else "플랫폼"      # 비교 기준이 아닌 다른 축
step_map = {                                            # 단계 이름 → (직전 단계 컬럼, 이번 단계 컬럼)
    "방문 → 식당조회":    ("세션수", "조회세션"),
    "식당조회 → 장바구니": ("조회세션", "장바구니세션"),
    "장바구니 → 결제시작": ("장바구니세션", "결제시작세션"),
    "결제시작 → 주문":    ("결제시작세션", "주문세션"),
}

d1, d2 = st.columns(2)
target = d1.selectbox("자세히 볼 집단", groups)          # 위 비교 기준에서 f에 있는 값만
step_label = d2.selectbox("단계", list(step_map))
den_col, num_col = step_map[step_label]

all_weeks = sorted(f["주시작일"].unique())              # 필터 기간 안의 주 목록

# 5-1. 고른 집단의 주별 단계 전환율 — 다른 축별로 쪼개 보기
sub = f[f[by] == target]
dd = sub.groupby(["주시작일", other])[[den_col, num_col]].sum()
full_idx = pd.MultiIndex.from_product([all_weeks, sorted(sub[other].unique())],
                                      names=["주시작일", other])
dd = dd.reindex(full_idx).reset_index()                 # 데이터가 없는 주도 빈 값으로 자리 확보
dd["전환율"] = (dd[num_col] / dd[den_col].where(dd[den_col] > 0) * 100).round(1)  # 분모 0이면 빈 값
dd = dd.rename(columns={den_col: "기준세션", num_col: "도달세션"})

fig6 = px.line(dd, x="주시작일", y="전환율", color=other, markers=True,
               hover_data={"기준세션": ":,", "도달세션": ":,"},
               title=f"① {target}의 주별 {step_label} 전환율 — {other}별로 쪼개 보기")
fig6.update_layout(xaxis_title="", yaxis_title=f"{step_label} 전환율 (%)")
st.plotly_chart(fig6, width="stretch")
st.caption("모든 갈래가 같은 시점에 함께 움직였다면, 그 축은 원인이 아닙니다.")

# 5-2. 같은 기간 주별 결제실패 건수 — 고른 집단 vs 그 외
pf = f.assign(구분=f[by].where(f[by] == target, "그 외"))
pf = pf.groupby(["주시작일", "구분"])["결제실패"].sum().reset_index()

fig7 = px.bar(pf, x="주시작일", y="결제실패", color="구분", barmode="group",
              category_orders={"구분": [target, "그 외"]},
              color_discrete_map={target: "#B71C1C", "그 외": "#9E9E9E"},
              hover_data={"결제실패": ":,"},
              title=f"② 주별 결제실패 건수 — {target} vs 그 외")
fig7.update_layout(xaxis_title="", yaxis_title="결제실패 (건)")
st.plotly_chart(fig7, width="stretch")
st.caption("문제가 생긴 시점과 함께 움직였다면 원인 후보입니다. 함께 움직였다고 원인이 확정되지는 않습니다.")