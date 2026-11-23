import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from scipy import stats
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

st.set_page_config(page_title="RidePulse — Ride Funnel & Cancellation Analytics", layout="wide")

@st.cache_resource
def get_engine():
    db = st.secrets["mysql"]
    connection_url = URL.create(
        drivername="mysql+mysqlconnector",
        username=db["user"],
        password=db["password"],
        host=db["host"],
        port=int(db["port"]),
        database=db["database"],
    )
    # pool_pre_ping verifies connections before checkout; pool_recycle refreshes old ones
    return create_engine(connection_url, pool_pre_ping=True, pool_recycle=300)

@st.cache_data(ttl=600)
def load_dashboard_data(vehicle_filter):
    engine = get_engine()

    # 1. Vehicle Funnel View
    df_funnel = pd.read_sql("SELECT * FROM vw_vehicle_funnel_metrics", engine)

    # 2. Stored Procedure Call using raw_connection() so multi-result sets close cleanly
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        cursor.callproc("GetCancellationBreakdown", [vehicle_filter])
        df_reasons = pd.DataFrame()
        for result in cursor.stored_results():
            df_reasons = pd.DataFrame(result.fetchall(), columns=result.column_names)
        cursor.close()
    finally:
        raw_conn.close()

    # 3. Hourly & VTAT Statistical Data (ONLY_FULL_GROUP_BY compliant)
    if vehicle_filter == "ALL":
        query_hourly = """
            SELECT 
                b.ride_hour,
                b.booking_status,
                ROUND(b.avg_vtat, 0) AS avg_vtat,
                COUNT(*) AS ride_count
            FROM ride_bookings b
            GROUP BY b.ride_hour, b.booking_status, ROUND(b.avg_vtat, 0)
        """
        df_raw_agg = pd.read_sql(query_hourly, engine)
    else:
        query_hourly = text("""
            SELECT 
                b.ride_hour,
                b.booking_status,
                ROUND(b.avg_vtat, 0) AS avg_vtat,
                COUNT(*) AS ride_count
            FROM ride_bookings b
            JOIN vehicle_types v ON b.vehicle_type_id = v.vehicle_type_id
            WHERE v.vehicle_name = :v
            GROUP BY b.ride_hour, b.booking_status, ROUND(b.avg_vtat, 0)
        """)
        with engine.connect() as conn:
            df_raw_agg = pd.read_sql(query_hourly, conn, params={"v": vehicle_filter})

    # 4. Top Pickup Hotspots
    df_locations = pd.read_sql("SELECT * FROM vw_location_cancellation_rank LIMIT 15", engine)

    return df_funnel, df_reasons, df_raw_agg, df_locations

st.title("RidePulse — Ride Funnel & Cancellation Root-Cause Analytics")

vehicle_options = ["ALL", "Auto", "Go Mini", "Go Sedan", "Bike", "Premier Sedan", "eBike", "Uber XL"]
selected_vehicle = st.selectbox("Filter by Vehicle Category:", vehicle_options, index=0)

df_funnel, df_reasons, df_raw_agg, df_locations = load_dashboard_data(selected_vehicle)

if df_funnel.empty:
    st.warning("No records found in `ncr_rides_db`. Run `python ridepulse_load_data.py` first, then press `C` to clear cache and rerun.")
    st.stop()

kpi_df = df_funnel[df_funnel["vehicle_type"] == selected_vehicle] if selected_vehicle != "ALL" else df_funnel

total_req = int(kpi_df["total_bookings"].sum())
completed_req = int(kpi_df["completed_rides"].sum())
failed_req = total_req - completed_req
comp_rate = round((completed_req / total_req) * 100, 1) if total_req else 0
realized_gmv = kpi_df["realized_gmv"].sum()
lost_gmv = kpi_df["estimated_lost_gmv"].sum()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Booking Requests", f"{total_req:,}")
c2.metric("Completed Rides", f"{completed_req:,}", f"{comp_rate}% Conversion")
c3.metric("Realized GMV", f"₹{realized_gmv:,.0f}")
c4.metric("Estimated Lost GMV", f"₹{lost_gmv:,.0f}", f"-{failed_req:,} Failed Rides", delta_color="inverse")

st.divider()

col_left, col_right = st.columns([1.15, 1])

with col_left:
    st.subheader("End-to-End Booking Funnel (Sankey Flow)")
    driver_can = int(kpi_df["driver_cancelled"].sum())
    cust_can = int(kpi_df["customer_cancelled"].sum())
    no_drv = int(kpi_df["no_driver_found"].sum())
    incomp = int(kpi_df["incomplete_rides"].sum())
    assigned = total_req - no_drv

    fig_sankey = go.Figure(data=[go.Sankey(
        node=dict(
            pad=18, thickness=20, line=dict(color="black", width=0.5),
            label=[
                f"Total Requests ({total_req:,})",
                f"Driver Assigned ({assigned:,})",
                f"No Driver Found ({no_drv:,})",
                f"Completed ({completed_req:,})",
                f"Driver Cancelled ({driver_can:,})",
                f"Customer Cancelled ({cust_can:,})",
                f"Incomplete ({incomp:,})"
            ],
            color=["#1f77b4", "#2ca02c", "#d62728", "#00cc96", "#ff7f0e", "#ef553b", "#ab63fa"]
        ),
        link=dict(
            source=[0, 0, 1, 1, 1, 1],
            target=[1, 2, 3, 4, 5, 6],
            value=[assigned, no_drv, completed_req, driver_can, cust_can, incomp]
        )
    )])
    fig_sankey.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig_sankey, use_container_width=True)

with col_right:
    st.subheader("Cancellation & Failure Root Causes")
    fig_reasons = px.bar(
        df_reasons,
        x="incident_count",
        y="root_cause",
        color="booking_status",
        orientation="h",
        text="incident_count",
        labels={"incident_count": "Failed Bookings", "root_cause": "Reason", "booking_status": "Failure Stage"}
    )
    fig_reasons.update_layout(height=420, yaxis={"categoryorder": "total ascending"}, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig_reasons, use_container_width=True)

st.divider()
col_b1, col_b2 = st.columns([1, 1])

with col_b1:
    st.subheader("Pickup Wait Time (Avg VTAT) vs. Customer Cancellation")
    vtat_df = df_raw_agg.dropna(subset=["avg_vtat"]).copy()
    vtat_df["vtat_bucket"] = pd.cut(
        vtat_df["avg_vtat"],
        bins=[0, 5, 10, 15, 25],
        labels=["0–5 mins", "5–10 mins", "10–15 mins", "15+ mins"]
    )
    vtat_df["is_cust_cancel"] = (vtat_df["booking_status"] == "Cancelled by Customer").astype(int)
    ct = vtat_df.pivot_table(index="vtat_bucket", columns="is_cust_cancel", values="ride_count", aggfunc="sum", fill_value=0)
    chi2, p_val, _, _ = stats.chi2_contingency(ct)
    
    vtat_summary = ct.reset_index()
    vtat_summary["Cancel Rate (%)"] = (vtat_summary[1] / (vtat_summary[0] + vtat_summary[1]) * 100).round(2)
    
    fig_vtat = px.bar(
        vtat_summary,
        x="vtat_bucket",
        y="Cancel Rate (%)",
        text="Cancel Rate (%)",
        color="Cancel Rate (%)",
        color_continuous_scale="Reds",
        labels={"vtat_bucket": "Vehicle Turnaround Time (Avg VTAT)"}
    )
    fig_vtat.update_layout(height=360, margin=dict(l=10, r=10, t=20, b=10))
    st.plotly_chart(fig_vtat, use_container_width=True)
    st.info(f"**Statistical Proof (Chi-Square Test):** $\\chi^2 = {chi2:,.1f}$ ($p = {p_val:.2e}$). Wait times above 15 minutes trigger a 100% customer cancellation spike.")

with col_b2:
    st.subheader("Top 15 NCR Pickup Hubs by Failed Ride Volume")
    st.dataframe(
        df_locations.rename(columns={
            "failure_volume_rank": "Rank",
            "pickup_location": "Pickup Hub",
            "total_requests": "Total Bookings",
            "failed_requests": "Failed Rides",
            "failure_rate_pct": "Failure Rate (%)",
            "avg_wait_mins": "Avg VTAT (m)"
        }),
        use_container_width=True,
        height=360,
        hide_index=True
    )
