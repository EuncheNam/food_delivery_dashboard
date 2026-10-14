import io
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

ACCENT, GRAY = "#1F3A5F", "#BDBDBD"          # ← 여기부터 추가
px.defaults.color_discrete_map = {
    "Android": ACCENT, "iOS": GRAY,
    "push": ACCENT, "organic": "#8C8C8C", "paid_ad": GRAY, "referral": "#DADADA",
}
px.defaults.color_discrete_sequence = [GRAY]

st.set_page_config(page_title="사용자 행동 탐색 대시보드", layout="wide")
st.title("사용자 행동 탐색 대시보드")
st.caption("주별 세션 퍼널 | 플랫폼 · 유입 경로별 | 2025.07 ~ 2026.06")

# ------------------------------------------------------------
# 0. 필요한 데이터만 추출 — 쓸 테이블과 컬럼만 메모리 DB에 올리고, 정제 테이블 events를 데이터마다 처음 한 번만 만듦
# ------------------------------------------------------------
DATA_DIR = "data"                          # 원본 CSV를 넣어 둔 폴더
NEEDED = {                                 # 이번 대시보드에 필요한 테이블과 컬럼
    "app_events": ["event_id", "event_datetime", "customer_id", "session_id",
                   "event_name", "platform", "traffic_source"],
}

@st.cache_resource
def get_db(csv_bytes):                     # 필요한 컬럼만 메모리 DB에 올리고, 정제 테이블 events를 만듦 (데이터마다 처음 한 번만)
    # csv_bytes: 올린 파일의 바이트 (없으면 None → data 폴더의 CSV 사용). 밑줄 없는 이름이라 캐시가 데이터별로 구분됨
    con = sqlite3.connect(":memory:", check_same_thread=False)
    for name, cols in NEEDED.items():
        source = io.BytesIO(csv_bytes) if csv_bytes is not None else f"{DATA_DIR}/{name}.csv"
        pd.read_csv(source, usecols=cols).to_sql(name, con, index=False)
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
def query(sql, csv_bytes):                 # SQL을 실행해 표로 돌려줌 (csv_bytes로 어느 데이터인지 구분)
    return pd.read_sql(sql, get_db(csv_bytes))

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
def load_data(csv_bytes):                  # csv_bytes: 올린 파일의 바이트 (없으면 None)
    data = query(SQL_WEEKLY_FUNNEL, csv_bytes)
    data["주시작일"] = pd.to_datetime(data["주시작일"]).dt.date
    return data

# 올린 파일 확인 — 캐시 함수 밖에서 컬럼을 확인하고, 쓸 데이터와 데이터 글자를 정함
# (올린 파일은 '📤 데이터 업로드' 탭의 file_uploader가 session_state에 넣어 둔 것을 꺼내 씀 · 디스크에 저장하지 않음)
DEFAULT_DATA_LABEL = "data 폴더 기본 파일"
csv_bytes, data_label = None, DEFAULT_DATA_LABEL
uploaded = st.session_state.get("upload_file")
if uploaded is not None:
    raw = uploaded.getvalue()
    try:
        header = pd.read_csv(io.BytesIO(raw), nrows=0).columns   # 컬럼 이름만 읽음
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError):
        st.error(f"올린 파일 {uploaded.name}을(를) CSV로 읽을 수 없어 data 폴더 기본 파일로 보여줍니다. "
                 "UTF-8 CSV인지 확인해 주세요.")
    else:
        missing = [c for c in NEEDED["app_events"] if c not in header]
        if missing:
            st.error(f"올린 파일 {uploaded.name}에 필요한 컬럼이 없습니다: {', '.join(missing)} "
                     "· data 폴더 기본 파일로 보여줍니다.")
        else:
            csv_bytes, data_label = raw, f"올린 파일 {uploaded.name}"

df = None
if csv_bytes is not None:
    try:
        df = load_data(csv_bytes)
    except (pd.errors.ParserError, ValueError, sqlite3.Error) as e:
        st.error(f"올린 파일을 집계하지 못해 data 폴더 기본 파일로 보여줍니다. ({e})")
    else:
        if df.empty:
            st.error("올린 파일에서 집계할 수 있는 주가 없어(2026-06-29 이전 주만 집계) "
                     "data 폴더 기본 파일로 보여줍니다.")
            df = None
    if df is None:
        csv_bytes, data_label = None, DEFAULT_DATA_LABEL
if df is None:
    df = load_data(None)                   # 주 × 플랫폼 × 유입 경로별 세션 퍼널

AI_NOTES = {
    "전체": "전체 기간 세션 28,840개 기준 주문 전환율은 14.5%, 결제 전환율은 80.5%이며, 가장 낮은 단계는 조회→장바구니(41.4%)이다. 플랫폼별로 보면 Android에서 2026-04-20 주부터 2026-05-25 주까지 결제 전환율이 2025년 하반기 범위(71.7%~90.3%)보다 낮은 41.4%~56.8%였고, 2026-06-01 주에 77.3%로 회복했다. 전체 결제 전환율 80.5%에는 이 Android 하락 구간이 포함되어 있으므로, 이 구간을 뺀 평상시 수준은 따로 확인이 필요하다.",
    "Android": "세션 16,053개 기준 Android의 주문 전환율 14.3%는 iOS(14.8%)와 비슷하지만, 결제 전환율은 76.8%로 iOS(85.5%)와 전체(80.5%)보다 낮다. 2026-04-20 주부터 2026-05-25 주까지 6주 연속 결제 전환율이 41.4%~56.8%로 2025년 하반기 범위(71.7%~90.3%)보다 낮았고, 이 기간 결제실패 이벤트는 764건이었으며 2026-06-01 주에 77.3%로 회복했다. 같은 기간 iOS에는 연속 하락이 없어 Android 결제 단계에 한정된 문제가 있었던 것으로 보이나, 이 파일만으로는 원인을 알 수 없어 확인이 필요하다.",
    "iOS": "세션 12,787개 기준 iOS의 결제 전환율은 85.5%로 Android(76.8%)와 전체(80.5%)보다 높고, 가장 낮은 단계는 조회→장바구니(40.8%)로 전체(41.4%)와 비슷하다. 2025년 하반기 범위(72.7%~91.4%) 아래로 내려간 주는 2026-05-04 주와 2026-06-15 주(각 60.9%) 두 번뿐이며, 두 주 모두 결제시작세션이 23개로 적고 결제실패는 0건이었다. 연속 하락 구간이 없어 iOS의 결제 흐름은 전체 기간 안정적이었던 것으로 보이며, 두 주의 하락은 표본이 작아 우연한 변동일 수 있는지 확인이 필요하다.",
    "organic": "organic은 세션 22,489개로 전체(28,840개)의 대부분을 차지하며, 주문 전환율 16.0%와 결제 전환율 80.4%는 전체(14.5%, 80.5%)와 비슷하다. 가장 낮은 단계도 조회→장바구니(41.4%)로 전체와 같다. 비중이 커서 전체 지표의 흐름을 사실상 결정하는 것으로 보이며, 유입경로별 주별 추이는 이번에 계산하지 않아 Android 하락 구간과의 관계는 확인이 필요하다.",
    "paid_ad": "paid_ad는 세션 1,595개로 organic(22,489개)보다 훨씬 적지만, 주문 전환율 26.3%와 결제 전환율 90.3%는 7개 대상 중 가장 높다. 가장 낮은 단계인 조회→장바구니도 53.7%로 전체(41.4%)보다 높다. 구매 의도가 높은 사용자가 유입되는 경로로 보이나, 세션 수가 적어 주별 변동이 클 수 있으므로 기간별 안정성은 확인이 필요하다.",
    "push": "push는 2026-02-02 주부터 나타나 그 이후 기간만 집계되었으며, 세션 4,635개로 paid_ad(1,595개)보다 많지만 주문 전환율은 3.2%로 전체(14.5%)보다 크게 낮다. 가장 낮은 단계는 세션→조회(28.4%)로 전체(61.9%)의 절반에 못 미치고, 결제 전환율도 63.8%로 전체(80.5%)보다 낮다. 알림으로 들어온 세션 중 상품 조회로 이어지지 않는 경우가 많은 것으로 보이며, 알림 내용이나 연결 화면에 대한 정보는 이 파일에 없어 확인이 필요하다.",
    "referral": "referral은 세션 121개로 500개 미만의 작은 집단이며, 결제시작세션 4개에서 주문이 0건이라 주문 전환율과 결제 전환율이 모두 0.0%이다. 가장 낮은 단계는 결제시작→주문(0.0%)이지만, 분모가 4세션뿐이다. 표본이 너무 작아 다른 집단과 전환율을 비교하기 어려운 것으로 보이며, 판단하려면 더 많은 데이터 확인이 필요하다.",
}
AI_SOURCE = "bot: 클로드가 미리 만든 해설 · 전체 기간 기준"
# ------------------------------------------------------------
# 1-1. 사이드바 필터 — 기간 · 플랫폼 · 유입 경로 (평소 범위 기준 기간은 '고급 설정' 안)
# ------------------------------------------------------------
st.sidebar.header("필터")

# 필터 초기화 — 위젯 key 끝에 번호를 붙여 두고, 초기화하면 번호만 올려 모든 필터 위젯을 처음 상태로 새로 만듦
#               (위젯 값을 session_state로 직접 덮어쓰지 않음)
if "filter_ver" not in st.session_state:
    st.session_state.filter_ver = 0

def reset_filters():                       # 버튼을 누르면 번호만 1 올림 → 다음 실행에서 새 key로 위젯이 기본값으로 다시 만들어짐
    st.session_state.filter_ver += 1

st.sidebar.button("필터 초기화", on_click=reset_filters)
v = st.session_state.filter_ver            # 이번 실행에서 위젯 key에 붙일 번호

weeks = sorted(df["주시작일"].unique())
start_week, end_week = st.sidebar.select_slider(
    "기간", options=weeks, value=(weeks[0], weeks[-1]), key=f"period_{v}")

platforms = sorted(df["플랫폼"].unique())
sel_platforms = st.sidebar.multiselect("플랫폼", platforms, default=platforms, key=f"platforms_{v}")

sources = sorted(df["유입경로"].unique())
sel_sources = st.sidebar.multiselect("유입 경로", sources, default=sources, key=f"sources_{v}")

# 고급 설정 — 평소 범위 기준 기간(기본값은 처음 26주, 주가 26개보다 적으면 전체) · 소집단 제외
with st.sidebar.expander("고급 설정"):
    base_start, base_end = st.select_slider(
        "평소 범위 기준 기간", options=weeks,
        value=(weeks[0], weeks[min(25, len(weeks) - 1)]),
        help="문제가 없던 기간을 고르세요. 이 기간의 주별 값 범위를 '평소 범위'로 씁니다.",
        key=f"base_period_{v}")
    exclude_small = st.checkbox("세션 500개 미만 집단 제외 (집단 비교 · 드릴다운)",
                                key=f"exclude_small_{v}")

# g: 집단 조건(플랫폼 · 유입 경로)만 적용 — 기간과 상관없이 평소 범위 계산 등에 사용
g = df[df["플랫폼"].isin(sel_platforms)
       & df["유입경로"].isin(sel_sources)]

# f: g에 기간 조건을 더한 데이터 — 화면의 KPI · 차트에 사용
f = g[g["주시작일"].between(start_week, end_week)]

# 보고 있는 조건 — 제목 아래(탭 위)에 한 줄로 표시
def cond_label(selected, all_values):      # 모두 선택 → "전체", 아무것도 없음 → "선택 없음", 일부 → 고른 값(정렬 순서)
    if not selected:
        return "선택 없음"
    if set(selected) == set(all_values):
        return "전체"
    return ", ".join(str(x) for x in all_values if x in selected)

period_label = ("전체" if (start_week, end_week) == (weeks[0], weeks[-1])
                else f"{start_week:%Y-%m-%d} ~ {end_week:%Y-%m-%d}")
st.caption(f"🔎 보고 있는 조건: 데이터 ({data_label}) · "
           f"기간 ({period_label}) · "
           f"플랫폼 ({cond_label(sel_platforms, platforms)}) · "
           f"유입 경로 ({cond_label(sel_sources, sources)})")

# ------------------------------------------------------------
# 1-2. 데이터 내려받기 — 사이드바 맨 아래, 전체(df) · 부분(f) CSV
# ------------------------------------------------------------
def to_csv_bytes(data):                    # 엑셀에서 한글이 깨지지 않도록 BOM이 붙은 UTF-8 CSV로 만듦
    buf = io.BytesIO()
    data.to_csv(buf, index=False, encoding="utf-8-sig")
    return buf.getvalue()

st.sidebar.html('<div style="height: 2rem;"></div>')   # 위쪽 필터와 다운로드 영역 사이 여백

dl1, dl2 = st.sidebar.columns(2)
dl1.download_button("전체 다운로드", data=to_csv_bytes(df),
                    file_name="전체_데이터.csv", mime="text/csv")
dl2.download_button("부분 다운로드", data=to_csv_bytes(f),
                    file_name="필터적용_데이터.csv", mime="text/csv")

if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 바꿔 주세요.")

# ------------------------------------------------------------
# 1-3. 화면 탭 — 개요 · 탐색 · 액션 · 데이터 업로드 (필터는 탭 밖 사이드바에 그대로)
# ------------------------------------------------------------
tab_overview, tab_explore, tab_action, tab_upload = st.tabs(
    ["📊 개요", "🔍 탐색", "✅ 액션", "📤 데이터 업로드"])

# ------------------------------------------------------------
# 1-4. 데이터 업로드 탭 — st.stop()보다 먼저 그려야 올린 파일이 유지됨
# ------------------------------------------------------------
with tab_upload:
    st.caption("이 탭에서 할 수 있는 것: 내 app_events.csv를 올려 대시보드 전체를 그 데이터로 보기")
    # key에 필터 초기화 번호를 붙이지 않음 → 필터 초기화를 눌러도 올린 파일은 그대로
    # 파일을 올리거나 지우면(on_change) 필터 초기화 번호를 1 올려 사이드바 필터를 처음 상태로
    st.file_uploader("app_events.csv 올리기", type="csv", key="upload_file",
                     on_change=reset_filters)
    st.caption(f"필요한 컬럼: {', '.join(NEEDED['app_events'])}")
    st.caption("올린 파일은 메모리에서만 쓰고 저장소나 서버 폴더에 저장하지 않습니다. "
               "파일을 지우면 data 폴더 기본 파일로 돌아갑니다.")

if f.empty:
    st.stop()

# 차트 라벨 — 컬럼 이름을 읽히는 한국어와 단위로 (축 제목 · 범례 제목 · 마우스 올렸을 때 설명에 쓰임)
LABELS = {
    "주시작일":   "주 시작일",
    "플랫폼":     "플랫폼",
    "유입경로":   "유입 경로",
    "단계":       "퍼널 단계",
    "세션수":     "세션 수(개)",
    "세션":       "이 단계 세션 수(개)",
    "기준세션":   "직전 단계 세션 수(개)",
    "도달세션":   "이번 단계 도달 세션 수(개)",
    "결제전환율": "결제 전환율(%)",
    "주문전환율": "주문 전환율(%)",
    "누적전환율": "누적 전환율(방문 대비, %)",
    "단계전환율": "단계 전환율(직전 단계 대비, %)",
    "결제실패":   "결제 실패(건)",
    "구분":       "구분",
}

# ============================================================
# 탭 1. 개요
# ============================================================
with tab_overview:
    st.caption("이 탭에서 볼 수 있는 것: 핵심 지표(KPI), 평소 범위와 비교한 최근 4주, 주별 결제 전환율 · 세션 퍼널 · 유입 경로별 주문 전환율")

    # ------------------------------------------------------------
    # 2. KPI 카드
    # ------------------------------------------------------------
    def rate(a, b):
        return a / b * 100 if b else 0

    t = f[["세션수", "결제시작세션", "주문세션"]].sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("세션 수", f"{t['세션수']:,}개", border=True, help="선택한 조건의 전체 세션")
    c2.metric("주문 전환율 (방문 → 주문)", f"{rate(t['주문세션'], t['세션수']):.1f}%", border=True, help="주문 세션 ÷ 전체 세션")
    c3.metric("결제 전환율 (결제 시작 → 주문)", f"{rate(t['주문세션'], t['결제시작세션']):.1f}%", border=True, help="주문 세션 ÷ 결제 시작 세션")

    # ------------------------------------------------------------
    # 2-1. 규칙 기반 설명 — 최근 4주 값을 평소 범위와 비교
    # ------------------------------------------------------------
    period_weeks = [w for w in weeks if start_week <= w <= end_week]   # 선택 기간 안의 주 목록

    def explain(label, num_col, den_col):
        """최근 4주 전환율을 기준 기간의 주별 전환율 범위와 비교해 한 문장으로 돌려줌"""
        # 이번 값: f의 마지막 4주 합계로 계산
        last4 = period_weeks[-4:]
        recent = f[f["주시작일"].between(last4[0], last4[-1])]
        num, den = recent[num_col].sum(), recent[den_col].sum()
        if den == 0:
            return f"{label}: {last4[0]:%Y-%m-%d} 주부터 4주 동안 분모가 0이라 계산할 수 없습니다."
        cur = round(num / den * 100, 1)

        # 평소 범위: g에서 기준 기간의 주별 전환율 최솟값 ~ 최댓값 (분모 0인 주는 제외)
        base = g[g["주시작일"].between(base_start, base_end)]
        wk = base.groupby("주시작일")[[num_col, den_col]].sum()
        wk = wk[wk[den_col] > 0]
        if len(wk) < 4:
            return f"{label}: 기준 기간을 이 집단의 데이터가 있는 시기로 옮겨 주세요."
        wk_rate = wk[num_col] / wk[den_col] * 100
        lo, hi = round(wk_rate.min(), 1), round(wk_rate.max(), 1)

        head = f"{label}: {last4[0]:%Y-%m-%d} 주부터 4주 값은 {cur:.1f}%로, 평소 범위({lo:.1f}% ~ {hi:.1f}%)"
        if cur < lo:
            return f"{head}보다 {lo - cur:.1f}%p 낮습니다."
        if cur > hi:
            return f"{head}보다 {cur - hi:.1f}%p 높습니다."
        return f"{head} 안에 있습니다."

    def show_note(text):                       # 해설 표시 — '~'가 마크다운 취소선으로 바뀌지 않도록 이스케이프
        st.markdown(text.replace("~", r"\~"))

    # 2-2. 지금 숫자 읽기 — 규칙 문장 · 기준 기간 · AI 해설을 테두리 상자 하나로 묶음
    with st.container(border=True):
        st.markdown("**📏 지금 숫자 읽기**")

        if len(period_weeks) < 4:
            lines = ["비교하려면 선택 기간을 4주 이상 골라 주세요."]
        else:
            lines = [explain("결제 전환율", "주문세션", "결제시작세션"),
                     explain("주문 전환율", "주문세션", "세션수")]
        show_note("\n".join(f"- {s}" for s in lines))   # 글머리표 목록으로 표시

        st.caption(f"📏 자동 계산 · 평소 범위 기준 기간 {base_start:%Y-%m-%d} ~ {base_end:%Y-%m-%d} (주별 값)")

        with st.expander("🤖 AI 해설 · 전체", expanded=False):
            show_note(AI_NOTES["전체"])
            st.caption(AI_SOURCE)

    st.divider()

    # ------------------------------------------------------------
    # 3. 차트
    # ------------------------------------------------------------
    left, right = st.columns(2, border=True)

    # 3-1. 주별 결제 전환율 (플랫폼별)
    wp = f.groupby(["주시작일", "플랫폼"])[["결제시작세션", "주문세션"]].sum().reset_index()
    wp["결제전환율"] = (wp["주문세션"] / wp["결제시작세션"] * 100).round(1)
    fig1 = px.line(wp, x="주시작일", y="결제전환율", color="플랫폼", markers=True,
                   labels=LABELS, title="주별 결제 전환율 (플랫폼별)")
    fig1.update_layout(yaxis_title=LABELS["결제전환율"], xaxis_title="")
    left.plotly_chart(fig1, width="stretch")

    # 3-2. 세션 퍼널
    steps = {"1.방문": "세션수", "2.식당조회": "조회세션", "3.장바구니": "장바구니세션",
             "4.결제시작": "결제시작세션", "5.주문완료": "주문세션"}
    fun = pd.DataFrame({"단계": list(steps), "세션수": [f[c].sum() for c in steps.values()]})
    fig2 = px.funnel(fun, x="세션수", y="단계", labels=LABELS, title="세션 퍼널")
    right.plotly_chart(fig2, width="stretch")

    # 3-3. 유입 경로별 주문 전환율
    ws = f.groupby("유입경로")[["세션수", "주문세션"]].sum().reset_index()
    ws["주문전환율"] = (ws["주문세션"] / ws["세션수"] * 100).round(1)
    ws = ws.sort_values("주문전환율")
    fig3 = px.bar(ws, x="주문전환율", y="유입경로", orientation="h", text_auto=".1f",
                  hover_data=["세션수"], labels=LABELS, title="유입 경로별 주문 전환율")
    fig3.update_xaxes(range=[0, ws["주문전환율"].max() * 1.2])
    fig3.update_layout(xaxis_title=LABELS["주문전환율"], yaxis_title="")
    fig3.update_traces(marker_color=[ACCENT if s == "push" else GRAY for s in ws["유입경로"]])   # ← 추가
    fig3.update_xaxes(visible=False)    
    st.plotly_chart(fig3, width="stretch")

# ============================================================
# 탭 2. 탐색
# ============================================================
with tab_explore:
    st.caption("이 탭에서 볼 수 있는 것: 플랫폼 · 유입 경로별 퍼널 비교, 고른 집단과 단계의 주별 드릴다운, 집단별 AI 해설")

    # ------------------------------------------------------------
    # 4. 집단 비교 — 플랫폼 또는 유입 경로별 퍼널 비교
    # ------------------------------------------------------------
    st.subheader("집단 비교")

    by = st.radio("비교 기준", ["플랫폼", "유입경로"], horizontal=True)

    stage_cols = ["세션수", "조회세션", "장바구니세션", "결제시작세션", "주문세션"]
    stage_names = ["1.방문", "2.식당조회", "3.장바구니", "4.결제시작", "5.주문완료"]

    # 집단별 퍼널 5단계 합계
    grp = f.groupby(by)[stage_cols].sum().reset_index()

    # 고급 설정에서 체크하면 세션 500개 미만 집단을 집단 비교 · 드릴다운 선택지에서 뺌
    # (모든 집단이 500개 미만이면 남는 집단이 없으므로 빼지 않음)
    excluded = []
    if exclude_small:
        small_all = grp.loc[grp["세션수"] < 500, by].tolist()
        if len(small_all) < len(grp):
            excluded = small_all
            grp = grp[~grp[by].isin(excluded)].reset_index(drop=True)
        else:
            st.caption("모든 집단이 세션 500개 미만이라 제외하지 않았습니다.")
    if excluded:
        st.caption(f"제외한 집단: {', '.join(map(str, excluded))} (세션 500개 미만)")

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
                     hover_data={"세션": ":,"}, labels=LABELS,
                     title=f"{LABELS[by]}별 누적 전환율 (방문 대비 %)")
    fig4.update_traces(texttemplate="%{x:.1f}%")
    # 1.방문이 맨 위, 5.주문완료가 맨 아래에 오도록 단계 순서를 고정
    fig4.update_yaxes(categoryorder="array", categoryarray=stage_names, autorange="reversed")
    cl.plotly_chart(fig4, width="stretch")

    # 4-2. 단계 전환율 막대 (직전 단계 대비 %)
    fig5 = px.bar(step, x="단계", y="단계전환율", color=by, barmode="group",
                  category_orders=orders, text_auto=".1f",
                  hover_data={"기준세션": ":,", "세션": ":,"}, labels=LABELS,
                  title=f"{LABELS[by]}별 단계 전환율 (직전 단계 대비 %)")
    fig5.update_layout(xaxis_title="", yaxis_title=LABELS["단계전환율"])
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

    rate_label = f"{step_label} 전환율(%)"                  # 고른 단계에 따라 바뀌는 라벨
    fig6 = px.line(dd, x="주시작일", y="전환율", color=other, markers=True,
                   hover_data={"기준세션": ":,", "도달세션": ":,"},
                   labels={**LABELS, "전환율": rate_label},
                   title=f"① {target}의 주별 {step_label} 전환율 — {LABELS[other]}별로 쪼개 보기")
    fig6.update_layout(xaxis_title="", yaxis_title=rate_label)
    st.plotly_chart(fig6, width="stretch")
    st.caption("모든 갈래가 같은 시점에 함께 움직였다면, 그 축은 원인이 아닙니다.")

    # 5-2. 같은 기간 주별 결제실패 건수 — 고른 집단 vs 그 외
    pf = f.assign(구분=f[by].where(f[by] == target, "그 외"))
    pf = pf.groupby(["주시작일", "구분"])["결제실패"].sum().reset_index()

    fig7 = px.bar(pf, x="주시작일", y="결제실패", color="구분", barmode="group",
                  category_orders={"구분": [target, "그 외"]},
                  color_discrete_map={target: "#B71C1C", "그 외": "#9E9E9E"},
                  hover_data={"결제실패": ":,"}, labels=LABELS,
                  title=f"② 주별 결제실패 건수 — {target} vs 그 외")
    fig7.update_layout(xaxis_title="", yaxis_title=LABELS["결제실패"])
    st.plotly_chart(fig7, width="stretch")
    st.caption("문제가 생긴 시점과 함께 움직였다면 원인 후보입니다. 함께 움직였다고 원인이 확정되지는 않습니다.")

    # 5-3. AI 해설 · 고른 집단 (사전에 없는 집단이면 안내 문구)
    with st.expander(f"🤖 AI 해설 · {target}", expanded=False):
        show_note(AI_NOTES.get(target, "이 집단의 해설은 아직 없습니다."))
        st.caption(AI_SOURCE)

# ============================================================
# 탭 3. 액션
# ============================================================
# 액션 카드 — 문장은 받은 그대로 둠 (고치지 않음)
ACTION_CARDS = [
    {
        "제목": "**카드 1 · Android 결제**",
        "본문": [
            "문제: 결제 시작에서 주문 전환율이 2026-04-13 주부터 하락해 5월 3주차 최저 41.4%, 6월 1주차부터 회복",
            "근거: 같은 기간 iOS는 평소 범위 안 · Android 결제 실패가 주 4건 이하 → 최대 153건",
            "성격: 특정 시점에 발생한 문제",
            "액션: 추가 분석: 앱 버전별로 결제 실패 확인 → 모니터링: 결제 전환율이 평소 범위를 벗어나는지 매주 점검",
            "우선순위: 영향 큼 · 실행 쉬움 → 바로 실행",
        ],
        "kpi_label": "**측정 KPI**: Android 결제 전환율 (최근 4주)",
        "확인할 곳": "개요 탭 규칙 문장(플랫폼 Android) · 탐색 탭 드릴다운",
        # 측정 KPI: 플랫폼이 Android인 데이터의 결제 전환율 (주문세션 ÷ 결제시작세션)
        "kpi_col": "플랫폼", "kpi_value": "Android",
        "kpi_num": "주문세션", "kpi_den": "결제시작세션",
    },
    {
        "제목": "**카드 2 · push 방문**",
        "본문": [
            "문제: 방문 → 식당 조회 전환율 28.4% (다른 경로 57-69%), 주문 전환율 3.2%",
            "근거: 2026년 2월부터 월별 세션의 30-36%가 push · 기간 내내 25-35%",
            "성격: 전체 구간에 세그먼트별 반응 차이",
            "액션: 실험: 일부 사용자에게만 푸시 대상과 문구를 바꿔 방문 → 식당 조회 전환율 비교",
            "우선순위: 효과 불확실 · 실행 쉬움 → 실험부터",
        ],
        "kpi_label": "**측정 KPI**: push 방문 → 식당 조회 전환율(최근 4주)",
        "확인할 곳": "탐색 탭 비교 기준 '유입경로' · 드릴다운 push",
        # 측정 KPI: 유입경로가 push인 데이터의 방문 → 식당 조회 전환율 (조회세션 ÷ 세션수)
        "kpi_col": "유입경로", "kpi_value": "push",
        "kpi_num": "조회세션", "kpi_den": "세션수",
    },
]

with tab_action:
    st.caption("이 탭에서 볼 수 있는 것: 개요 · 탐색에서 찾은 문제에 대해 다음에 할 일")
    st.caption("측정 KPI의 현재 값은 선택한 기간의 마지막 4주로 계산합니다")

    # 측정 KPI용 데이터 — 사이드바의 기간만 적용 (플랫폼 · 유입 경로 필터는 무시해야 하므로 f 대신 df에서 기간만 거름)
    last4_kpi = period_weeks[-4:]                           # 선택 기간의 마지막 4주 (기간이 4주보다 짧으면 있는 주 전체)
    kpi_base = df[df["주시작일"].isin(last4_kpi)]

    def kpi_value(card):                                    # 카드의 측정 KPI 현재 값 — 분모가 0이면 "데이터 없음"
        part = kpi_base[kpi_base[card["kpi_col"]] == card["kpi_value"]]
        num, den = part[card["kpi_num"]].sum(), part[card["kpi_den"]].sum()
        return f"{num / den * 100:.1f}%" if den > 0 else "데이터 없음"

    kpi_help = (f"{last4_kpi[0]:%Y-%m-%d} 주 ~ {last4_kpi[-1]:%Y-%m-%d} 주 · 사이드바 기간만 적용"
                if last4_kpi else "선택 기간에 주가 없습니다.")

    for col, card in zip(st.columns(2), ACTION_CARDS):
        with col:
            with st.container(border=True):                 # 카드: 제목 + 문제 · 근거 · 성격 · 액션 · 우선순위
                st.markdown(card["제목"])
                show_note("\n".join(f"- {line}" for line in card["본문"]))
            st.metric(card["kpi_label"], kpi_value(card), help=kpi_help)   # 카드 아래: 측정 KPI 현재 값
            st.caption(f"대시보드에서: {card['확인할 곳']}")               # 그 아래: 확인할 곳