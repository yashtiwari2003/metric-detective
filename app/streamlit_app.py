"""
Streamlit Web Application for Metric Detective.
Interactive UI featuring:
  1. KPI Monitor: Interactive time-series charts with anomaly detection markers.
  2. Investigate: Step-by-step root-cause analysis with expandable evidence & SQL queries.
  3. Ask a Follow-up: Context-aware interactive Q&A powered by SQL Guard and Verifier.
  4. Evaluation: Live accuracy benchmark comparison across 3 architectural variants.
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import text
from dotenv import load_dotenv

# Setup root path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / '.env')

from src.pipeline.investigate import get_engine, load_config, investigate_anomaly, investigate_period
from src.detection.anomaly import detect_anomalies, detect_all_anomalies, Anomaly
from src.ai.llm_client import get_usage_stats, call_llm
from src.safety.sql_guard import validate_sql, execute_safe_query
from src.ai.schema_context import build_schema_context

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Page Config ---
st.set_page_config(
    page_title='Metric Detective | AI Root-Cause Analyst',
    page_icon='🔍',
    layout='wide',
    initial_sidebar_state='expanded'
)

# --- Resource Caching ---
@st.cache_resource
def get_db_engine():
    return get_engine()

@st.cache_data
def get_app_config():
    return load_config()

@st.cache_data
def load_kpi_daily(_engine):
    try:
        query = "SELECT * FROM kpi_daily ORDER BY order_date"
        df = pd.read_sql(query, _engine)
        df['order_date'] = pd.to_datetime(df['order_date']).dt.date
        return df
    except Exception as e:
        logger.error(f"Error loading kpi_daily: {e}")
        return pd.DataFrame()

engine = get_db_engine()
config = get_app_config()
kpi_df = load_kpi_daily(engine)

# --- Sidebar ---
with st.sidebar:
    st.title("🔍 Metric Detective")
    st.caption("AI-Powered Root-Cause Analyst")
    st.divider()

    st.subheader("⚙️ AI Configuration")
    provider = st.selectbox(
        "LLM Provider", 
        ["gemini", "openai", "groq"], 
        index=0 if os.getenv("LLM_PROVIDER") == "gemini" else 1
    )
    default_model = os.getenv("LLM_MODEL", "gemini-3.5-flash-lite")
    model_name = st.text_input("LLM Model", value=default_model)
    
    # Update environment dynamically
    os.environ["LLM_PROVIDER"] = provider
    os.environ["LLM_MODEL"] = model_name

    st.divider()
    st.subheader("📊 Session Telemetry")
    usage = get_usage_stats()
    col_u1, col_u2 = st.columns(2)
    with col_u1:
        st.metric("Total Calls", f"{usage.get('total_calls', 0)}")
        st.metric("Tokens In", f"{usage.get('total_input_tokens', 0):,}")
    with col_u2:
        st.metric("Estimated Cost", f"${usage.get('total_cost', 0.0):.4f}")
        st.metric("Tokens Out", f"{usage.get('total_output_tokens', 0):,}")

    st.divider()
    if 'report_content' in st.session_state and st.session_state['report_content']:
        st.download_button(
            label="📥 Download Active Report",
            data=st.session_state['report_content'],
            file_name=f"investigation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
            mime="text/markdown",
            use_container_width=True
        )

    st.info("💡 **Core Architecture**: The LLM proposes hypotheses and writes SQL. The engine executes queries, tests statistics, and the Verifier audits every figure against raw data.")

# --- Navigation Tabs ---
page = st.radio(
    "Navigation", 
    ["📈 KPI Monitor", "🕵️ Investigate", "💬 Ask a Follow-up", "🧪 Benchmark Evaluation"], 
    horizontal=True,
    label_visibility="collapsed"
)

# =====================================================================
# Page 1: KPI Monitor
# =====================================================================
if page == "📈 KPI Monitor":
    st.header("📈 Business KPI Monitor")
    st.markdown("Automated anomaly detection across daily operational KPIs using **STL decomposition** (weekly seasonality) + **robust MAD z-scores**.")

    if kpi_df.empty:
        st.error("No daily KPI data found. Run ETL pipeline first.")
    else:
        min_d = kpi_df['order_date'].min()
        max_d = kpi_df['order_date'].max()

        col_f1, col_f2 = st.columns([2, 1])
        with col_f1:
            date_range = st.date_input(
                "Filter Date Range", 
                value=(min_d, max_d), 
                min_value=min_d, 
                max_value=max_d
            )
        with col_f2:
            kpi_options = [c for c in kpi_df.columns if c != 'order_date']
            selected_kpis = st.multiselect("Select KPIs to Display", kpi_options, default=kpi_options[:4])

        if len(date_range) == 2 and selected_kpis:
            start_sel, end_sel = date_range
            mask = (kpi_df['order_date'] >= start_sel) & (kpi_df['order_date'] <= end_sel)
            df_filtered = kpi_df.loc[mask]

            cols_layout = st.columns(2)
            for idx, kpi in enumerate(selected_kpis):
                with cols_layout[idx % 2]:
                    fig = go.Figure()
                    
                    # Main line
                    fig.add_trace(go.Scatter(
                        x=df_filtered['order_date'],
                        y=df_filtered[kpi],
                        mode='lines',
                        name=kpi.replace('_', ' ').title(),
                        line=dict(color='#1f77b4', width=2)
                    ))
                    
                    # Detect anomalies for selected KPI
                    anoms = detect_anomalies(engine, kpi, config)
                    anom_in_range = [a for a in anoms if start_sel <= a.date <= end_sel]
                    
                    if anom_in_range:
                        a_dates = [a.date for a in anom_in_range]
                        a_vals = [a.actual for a in anom_in_range]
                        a_texts = [f"Actual: {a.actual:,.2f}<br>Exp: {a.expected:,.2f}<br>z: {a.z_score:.2f}" for a in anom_in_range]
                        
                        fig.add_trace(go.Scatter(
                            x=a_dates,
                            y=a_vals,
                            mode='markers',
                            name='Anomaly (|z| > 3.0)',
                            marker=dict(color='#d62728', size=8, symbol='circle'),
                            text=a_texts,
                            hoverinfo='text+x'
                        ))

                    fig.update_layout(
                        title=f"<b>{kpi.replace('_', ' ').title()}</b> ({len(anom_in_range)} anomalies flagged)",
                        template="plotly_white",
                        height=350,
                        margin=dict(l=40, r=20, t=50, b=40),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                    )
                    st.plotly_chart(fig, use_container_width=True)

# =====================================================================
# Page 2: Investigate
# =====================================================================
elif page == "🕵️ Investigate":
    st.header("🕵️ Root-Cause Investigation")
    st.markdown("Trigger an automated deep-dive investigation on any metric anomaly period.")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        kpi_list = list(config.get('kpis', {}).keys()) or ['revenue', 'orders', 'avg_order_value', 'late_delivery_rate']
        selected_kpi = st.selectbox("Select KPI to Investigate", kpi_list, index=0)
    with col2:
        default_start = datetime(2018, 5, 1).date()
        default_end = datetime(2018, 5, 7).date()
        inv_range = st.date_input("Select Anomaly Period", value=(default_start, default_end))
    with col3:
        st.write("")
        st.write("")
        run_btn = st.button("🚀 Run Investigation", type="primary", use_container_width=True)

    if run_btn:
        if len(inv_range) != 2:
            st.error("Please select a valid start and end date.")
        else:
            s_str = inv_range[0].strftime('%Y-%m-%d')
            e_str = inv_range[1].strftime('%Y-%m-%d')

            with st.status(f"Investigating {selected_kpi} anomaly ({s_str} to {e_str})...", expanded=True) as status:
                st.write("📊 Step 1: Running dimensional contribution analysis across regions, categories & sellers...")
                st.write("🧠 Step 2: Extracting schema context & prompting AI Hypothesis Agent...")
                st.write("🛡️ Step 3: Generating SQL queries with strict safety guard validation...")
                st.write("📐 Step 4: Executing queries & running statistical significance tests (t-test / z-test / chi-square)...")
                st.write("🔎 Step 5: Auditing narrative claims against query results via AI Verifier...")

                try:
                    report_text, metadata = investigate_period(engine, selected_kpi, s_str, e_str, config)
                    st.session_state['report_content'] = report_text
                    st.session_state['last_metadata'] = metadata
                    status.update(label="✅ Investigation Completed Successfully!", state="complete", expanded=False)
                except Exception as e:
                    status.update(label=f"❌ Error: {e}", state="error")
                    st.error(f"Investigation failed: {e}")

    # Display Active Report
    if 'report_content' in st.session_state and st.session_state['report_content']:
        st.divider()
        st.subheader("📑 Evidence-Backed Investigation Report")
        
        # Meta badge bar
        meta = st.session_state.get('last_metadata', {})
        if meta:
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("Anomaly KPI", meta.get('kpi', ''))
            b2.metric("Period", meta.get('date', ''))
            b3.metric("Change", f"{meta.get('pct_change', 0.0):+.1f}%")
            b4.metric("Verification Score", f"{meta.get('verification_score', 1.0):.0%}")

        st.markdown(st.session_state['report_content'])

# =====================================================================
# Page 3: Ask a Follow-up
# =====================================================================
elif page == "💬 Ask a Follow-up":
    st.header("💬 Interactive Follow-up Chat")
    st.markdown("Ask natural-language follow-up questions. The AI generates safe, audited read-only SQL, executes it against DuckDB, and provides verified answers.")

    if 'chat_messages' not in st.session_state:
        st.session_state['chat_messages'] = [
            {"role": "assistant", "content": "Hello! I am Metric Detective. Ask me any follow-up question regarding recent anomalies, seller distributions, category drops, or customer trends."}
        ]

    for msg in st.session_state['chat_messages']:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if "df" in msg and msg["df"] is not None:
                st.dataframe(msg["df"], use_container_width=True)

    if user_input := st.chat_input("e.g. Which seller in SP had the highest late delivery count in May 2018?"):
        st.session_state['chat_messages'].append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            with st.spinner("Generating query & querying database..."):
                schema_context = build_schema_context(engine)
                system_prompt = f"""You are a SQL and data analyst assistant. 
Given the schema below, write a single read-only SELECT query to answer the user request.
Schema:
{schema_context}

Return strictly a JSON object:
{{"query": "SELECT ... LIMIT 10", "explanation": "Brief description"}}
"""
                try:
                    from pydantic import BaseModel
                    class ChatSQL(BaseModel):
                        query: str
                        explanation: str
                    
                    sql_res = call_llm(system=system_prompt, user=user_input, response_model=ChatSQL)
                    is_valid, cleaned_or_err = validate_sql(sql_res.query)
                    
                    if not is_valid:
                        ans_text = f"🛡️ **SQL Guard Blocked Query**: {cleaned_or_err}"
                        st.markdown(ans_text)
                        st.session_state['chat_messages'].append({"role": "assistant", "content": ans_text})
                    else:
                        df_res = execute_safe_query(engine, cleaned_or_err)
                        ans_text = f"**{sql_res.explanation}**\n\n```sql\n{cleaned_or_err}\n```"
                        st.markdown(ans_text)
                        st.dataframe(df_res, use_container_width=True)
                        st.session_state['chat_messages'].append({
                            "role": "assistant", 
                            "content": ans_text, 
                            "df": df_res
                        })
                except Exception as e:
                    err_msg = f"Error processing query: {e}"
                    st.error(err_msg)
                    st.session_state['chat_messages'].append({"role": "assistant", "content": err_msg})

# =====================================================================
# Page 4: Evaluation
# =====================================================================
elif page == "🧪 Benchmark Evaluation":
    st.header("🧪 Evaluation Benchmark")
    eval_file = PROJECT_ROOT / "reports" / "eval_results.md"

    if eval_file.exists():
        with open(eval_file, "r", encoding="utf-8") as f:
            st.markdown(f.read())
    else:
        st.info("Evaluation results not found. Run `python -m src.evaluation.run_eval` to execute the benchmark.")
