"""Streamlit dashboard for Brazil's economic indicators."""
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data_fetcher import BRT, ROOT, SERIES, START, read_snapshot, read_state, refresh

st.set_page_config(page_title="Brazil Economic Performance", layout="wide")
st.sidebar.header("Dashboard controls")
if st.sidebar.button("Refresh from official sources", width="stretch"):
    with st.spinner("Retrieving and validating six official series…"):
        try:
            refresh()
        except Exception as exc:
            st.sidebar.error(f"Refresh could not finish: {exc}")
if st.sidebar.button("Restore default period", width="stretch"):
    st.session_state.pop("period", None)

st.title("Brazil Economic Performance")
st.markdown("How is Brazil balancing price stability and economic growth as human capital develops?")
try:
    state = read_state()
    data = read_snapshot(state=state)
except (OSError, ValueError) as exc:
    st.error(f"The saved data could not be opened: {exc}")
    st.stop()


def timestamp(value):
    if not value:
        return "Unavailable"
    return datetime.fromisoformat(value).astimezone(BRT).strftime("%d %b %Y, %H:%M:%S %Z (UTC%z)")


attempt = state.get("last_attempt", {})
st.sidebar.caption(f"Last successful refresh · {timestamp(state.get('last_successful_refresh'))}")
if attempt.get("status") == "success":
    st.sidebar.success("Latest refresh succeeded")
elif attempt:
    st.warning(f"Refresh failed at {timestamp(attempt.get('completed_at'))}. "
               "The dashboard is showing the most recent validated snapshot." if not data.empty
               else "Refresh failed before a validated snapshot was available.")
    for key, entry in attempt["indicators"].items():
        if entry.get("error"):
            st.error(f"{SERIES[key].name} ({entry['source']}) · {entry['error']}")
else:
    st.sidebar.info("No refresh has been recorded")

if data.empty:
    st.info("Use the refresh button or run `python data_fetcher.py` to load the first snapshot.")
    st.stop()

latest_date = data["date"].max().date()
selection = st.sidebar.date_input(
    "Historical period", value=(START, latest_date), min_value=START, max_value=latest_date, key="period"
)
partial = len(selection) != 2
start, end = (START, latest_date) if partial else selection
if partial:
    st.sidebar.caption("Choose an end date to apply the filter.")
subset = data.loc[data["date"].between(pd.Timestamp(start), pd.Timestamp(end))]
st.sidebar.download_button("Download selected observations", subset.to_csv(index=False),
                           "brazil_selected.csv", "text/csv", width="stretch")
st.sidebar.download_button("Download full dataset", data.to_csv(index=False),
                           "brazil_economic_data.csv", "text/csv", width="stretch")

histories = {key: data.loc[data["indicator"].eq(key)] for key in SERIES}


def observation_label(stamp, key):
    period = SERIES[key].period
    if period == "Y":
        return str(stamp.year)
    if period == "Q":
        return f"Q{stamp.quarter} {stamp.year}"
    if key == "unemployment":
        return f"{(stamp - pd.DateOffset(months=2)):%b}–{stamp:%b %Y}"
    return stamp.strftime("%b %Y" if period == "M" else "%d %b %Y")


st.header("Economic overview")
st.caption("Latest official observations across all dates. Changes compare consecutive published observations.")
keys = list(SERIES)
for offset in (0, 3):
    for key, card in zip(keys[offset:offset + 3], st.columns(3)):
        meta = SERIES[key]
        past, latest = histories[key].iloc[-2:].itertuples(index=False)
        change = latest.value - past.value
        unit = "pp" if meta.unit.startswith("%") else meta.unit
        card.metric(meta.name, f"{latest.value:,.{meta.precision}f} {meta.unit}",
                    f"{change:+.{meta.precision}f} {unit}", delta_color="off", border=True)
        card.caption(observation_label(latest.date, key))


def draw(chart_id, keys, unit):
    chart = go.Figure()
    for key in keys:
        meta = SERIES[key]
        rows = subset.loc[subset["indicator"].eq(key)]
        if rows.empty:
            continue
        mode = "markers" if meta.period == "Y" else "lines+markers" if meta.period != "D" else "lines"
        chart.add_scatter(
            x=rows["date"], y=rows["value"], name=meta.name, mode=mode,
            line=dict(color=meta.color, width=2, shape="hv" if key == "selic" else "linear"),
            marker=dict(color=meta.color, size=8 if meta.period == "Y" else 5),
            customdata=[observation_label(stamp, key) for stamp in rows["date"]],
            hovertemplate=f"%{{customdata}}<br>%{{y:.{meta.precision}f}} {meta.unit}<extra>{meta.name}</extra>",
        )
    if chart.data:
        chart.update_layout(height=330, margin=dict(l=12, r=15, t=16, b=20),
                            yaxis_title=unit, hovermode="closest",
                            legend=dict(orientation="h", y=1.12), showlegend=len(keys) > 1)
        st.plotly_chart(chart, width="stretch", key=chart_id, config={"displaylogo": False})
    else:
        st.info("No published observations fall in the selected period.")
    for key in keys:
        meta = SERIES[key]
        latest = histories[key]["date"].iloc[-1]
        label = observation_label(latest, key)
        st.caption(f"**{meta.name}** · {meta.definition} Unit: {meta.unit}. "
                   f"Frequency: {meta.frequency}. Latest: {label}. "
                   f"{meta.note} [{meta.agency}]({meta.link})")


st.header("Macroeconomic performance")
st.subheader("Inflation and monetary policy")
draw("comparison", ["inflation", "selic"], "Percent")
st.caption("The two rates retain their original observation frequencies. Compare timing and direction over the "
           "selected period. The chart alone cannot establish a causal effect.")
for pair in [("inflation", "gdp"), ("selic", "unemployment"), ("exchange",)]:
    for key, panel in zip(pair, st.columns(2) if len(pair) == 2 else [st.container()]):
        with panel:
            st.subheader(SERIES[key].name)
            draw(key, [key], SERIES[key].unit)

st.header("Human capital development")
st.subheader(SERIES["education"].name)
st.markdown("Adult schooling changes over years and tracks long-term human capital development. "
            "Short-term monetary policy responses are outside this indicator’s scope.")
draw("education", ["education"], SERIES["education"].unit)

with st.expander("Data verification and refresh details"):
    verification_path = ROOT / "verification/verification.csv"
    if verification_path.exists():
        evidence = pd.read_csv(verification_path)
        if not evidence["snapshot"].eq(state["snapshot"]).all():
            st.caption("The verification sample refers to an earlier snapshot.")
        st.dataframe(evidence.drop(columns="snapshot"), hide_index=True, width="stretch",
                     column_config={"source_url": st.column_config.LinkColumn("Official reference")})
    if attempt:
        st.json(attempt, expanded=False)
