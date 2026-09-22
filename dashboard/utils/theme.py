#!/usr/bin/env python3
"""
Netwroxia — Phase E11a: Theme

Central CSS for the combined dashboard. Extracted from the original
app.py so app_v2.py stays focused on layout. Both apps share the same
visual language.

Usage:
    from utils.theme import inject_theme
    inject_theme()
"""

import streamlit as st

THEME_HTML = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600;700&display=swap" rel="stylesheet">

<style>
.stApp {
    background:
      radial-gradient(1200px 600px at 10% -10%, rgba(34,211,238,0.08), transparent 60%),
      radial-gradient(900px 500px at 100% 0%, rgba(168,85,247,0.07), transparent 60%),
      #0a0e1a;
    color: #e5e7eb;
    font-family: 'Inter', sans-serif;
}
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
#MainMenu, footer, header [data-testid="stToolbar"] { visibility: hidden; }
.block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1400px; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: #0a0e1a; }
::-webkit-scrollbar-thumb { background: #1f2937; border-radius: 8px; }
::-webkit-scrollbar-thumb:hover { background: #334155; }
h1,h2,h3,h4 { color: #f1f5f9; letter-spacing: -0.01em; }
.stMarkdown p, .stMarkdown li { color: #cbd5e1; }
code, kbd, .mono { font-family: 'JetBrains Mono', monospace !important; }

.nx-header {
    background: linear-gradient(135deg, #0f172a 0%, #111827 60%, #0b1220 100%);
    border: 1px solid #1f2937; border-radius: 16px;
    padding: 20px 24px; display: flex; align-items: center; justify-content: space-between;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.03);
    position: relative; overflow: hidden;
}
.nx-brand { display: flex; align-items: center; gap: 14px; }
.nx-logo {
    width: 52px; height: 52px; border-radius: 12px;
    background: linear-gradient(135deg, #22d3ee 0%, #a855f7 100%);
    display: grid; place-items: center; font-size: 28px;
    box-shadow: 0 0 24px rgba(34,211,238,0.35);
}
.nx-title { font-size: 26px; font-weight: 700; color: #f8fafc; margin: 0; }
.nx-sub { font-size: 12px; color: #94a3b8; font-family: 'JetBrains Mono', monospace;
    text-transform: uppercase; letter-spacing: 0.12em; margin-top: 2px; }

.nx-pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 12px; border-radius: 999px;
    font-family: 'JetBrains Mono', monospace; font-size: 11px; font-weight: 600;
    border: 1px solid #1f2937; background: #0b1220; color: #cbd5e1;
    letter-spacing: 0.08em;
}
.nx-pill.ok { border-color: rgba(34,197,94,0.4); color: #4ade80; box-shadow: 0 0 12px rgba(34,197,94,0.15); }
.nx-pill.brand { border-color: rgba(34,211,238,0.4); color: #67e8f9; }
.nx-dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e;
    box-shadow: 0 0 8px #22c55e; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100% {opacity:1;} 50% {opacity:0.4;} }

div[data-testid="stMetric"] {
    background: linear-gradient(180deg, #111827 0%, #0d1424 100%);
    border: 1px solid #1f2937; border-radius: 12px;
    padding: 14px 16px; box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    transition: border-color .2s, transform .2s;
}
div[data-testid="stMetric"]:hover { border-color: #334155; transform: translateY(-1px); }
div[data-testid="stMetricLabel"] { color: #94a3b8 !important; font-size: 11px !important;
    text-transform: uppercase; letter-spacing: 0.1em; font-weight: 600; }
div[data-testid="stMetricValue"] { font-family: 'JetBrains Mono', monospace !important;
    color: #f1f5f9 !important; font-weight: 700 !important; }

.stButton > button[kind="primary"] {
    width: 100%;
    background: linear-gradient(135deg, #22d3ee 0%, #a855f7 100%);
    border: none; color: #0a0e1a;
    font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 14px 20px; border-radius: 12px; font-size: 14px;
    box-shadow: 0 8px 24px rgba(34,211,238,0.25), 0 0 0 1px rgba(34,211,238,0.3);
    transition: transform .15s, box-shadow .15s;
}
.stButton > button[kind="primary"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 12px 32px rgba(168,85,247,0.4), 0 0 0 1px rgba(168,85,247,0.5);
}

.stTabs [data-baseweb="tab-list"] { gap: 6px; background: #0b1220; padding: 6px;
    border-radius: 14px; border: 1px solid #1f2937; flex-wrap: wrap; }
.stTabs [data-baseweb="tab"] { background: transparent; color: #94a3b8;
    padding: 10px 16px; border-radius: 10px; font-weight: 600; font-size: 13px;
    border: none; transition: all .2s; }
.stTabs [data-baseweb="tab"]:hover { color: #e2e8f0; background: rgba(255,255,255,0.03); }
.stTabs [aria-selected="true"] {
    background: linear-gradient(135deg, rgba(34,211,238,0.15), rgba(168,85,247,0.15)) !important;
    color: #67e8f9 !important;
    box-shadow: 0 0 0 1px rgba(34,211,238,0.4), 0 4px 12px rgba(34,211,238,0.15);
}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display: none; }

.nx-router {
    background: linear-gradient(180deg, #111827 0%, #0d1424 100%);
    border: 1px solid #1f2937; border-left: 4px solid var(--stripe, #22c55e);
    border-radius: 12px; padding: 14px 18px; margin-bottom: 10px;
    display: flex; align-items: center; gap: 20px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
    transition: transform .15s, border-color .15s;
}
.nx-router:hover { transform: translateX(2px); }
.nx-router-name { font-family: 'JetBrains Mono', monospace; font-weight: 700;
    color: #f1f5f9; font-size: 15px; min-width: 180px; }
.nx-status-chip {
    display: inline-flex; align-items: center; gap: 6px;
    font-family: 'JetBrains Mono', monospace; font-size: 10px;
    font-weight: 700; letter-spacing: 0.1em;
    padding: 4px 10px; border-radius: 999px;
    background: color-mix(in srgb, var(--stripe, #22c55e) 15%, transparent);
    color: var(--stripe, #22c55e);
    border: 1px solid color-mix(in srgb, var(--stripe, #22c55e) 40%, transparent);
    margin-top: 4px;
}
.nx-status-chip .nx-dot { background: var(--stripe, #22c55e); box-shadow: 0 0 8px var(--stripe, #22c55e); }
.nx-metric { display: flex; flex-direction: column; gap: 2px; flex: 1; }
.nx-metric-label { font-size: 10px; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.1em; font-weight: 600; }
.nx-metric-value { font-family: 'JetBrains Mono', monospace; font-weight: 700;
    color: #f1f5f9; font-size: 16px; }
.nx-bar { width: 100%; height: 4px; background: #1f2937; border-radius: 4px;
    margin-top: 4px; overflow: hidden; }
.nx-bar > div { height: 100%; border-radius: 4px;
    background: linear-gradient(90deg, var(--stripe, #22c55e), color-mix(in srgb, var(--stripe, #22c55e) 60%, white));
    box-shadow: 0 0 8px var(--stripe, #22c55e); }

.nx-section { display: flex; align-items: center; gap: 10px; margin: 24px 0 12px 0; }
.nx-section-bar { width: 4px; height: 22px; border-radius: 2px;
    background: linear-gradient(180deg, #22d3ee, #a855f7);
    box-shadow: 0 0 8px rgba(34,211,238,0.5); }
.nx-section-title { font-size: 18px; font-weight: 700; color: #f1f5f9; }
.nx-section-kicker { font-family: 'JetBrains Mono', monospace; font-size: 10px;
    color: #64748b; text-transform: uppercase; letter-spacing: 0.15em; margin-left: 8px; }

.nx-copilot {
    background: linear-gradient(135deg, rgba(168,85,247,0.08), rgba(34,211,238,0.05));
    border: 1px solid rgba(168,85,247,0.3);
    border-radius: 14px; padding: 20px;
    box-shadow: 0 8px 32px rgba(168,85,247,0.1);
}
.nx-copilot-header { display: flex; align-items: center; gap: 10px; margin-bottom: 14px;
    padding-bottom: 12px; border-bottom: 1px solid rgba(168,85,247,0.2); }
.nx-copilot-icon { width: 32px; height: 32px; border-radius: 8px;
    background: linear-gradient(135deg, #a855f7, #22d3ee);
    display: grid; place-items: center; font-size: 16px;
    box-shadow: 0 0 16px rgba(168,85,247,0.4); }
.nx-copilot-title { font-weight: 700; color: #f1f5f9; font-size: 15px; }
.nx-copilot-row { margin: 8px 0; color: #cbd5e1; font-size: 14px; line-height: 1.6; }
.nx-copilot-row b { color: #67e8f9; font-family: 'JetBrains Mono', monospace;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em;
    display: block; margin-bottom: 2px; }

.nx-tile { background: #0b1220; border: 1px solid #1f2937; border-radius: 10px; padding: 10px 12px; }
.nx-tile-label { font-size: 10px; color: #64748b; text-transform: uppercase;
    letter-spacing: 0.1em; font-weight: 600; }
.nx-tile-value { font-family: 'JetBrains Mono', monospace; font-weight: 700;
    color: #f1f5f9; font-size: 15px; margin-top: 4px; }
.nx-tile-value.urgent { color: #f87171; }
.nx-tile-value.warn { color: #fbbf24; }
.nx-tile-value.ok { color: #4ade80; }

.streamlit-expanderHeader, [data-testid="stExpander"] summary {
    background: #0b1220 !important; border: 1px solid #1f2937 !important;
    border-radius: 10px !important; font-weight: 600 !important; color: #e2e8f0 !important; }
[data-testid="stExpander"] { border: none !important; }
hr, [data-testid="stDivider"] { border-color: #1f2937 !important;
    background: linear-gradient(90deg, transparent, #1f2937, transparent) !important;
    height: 1px !important; border: none !important; }

.nx-footer { display: flex; align-items: center; justify-content: center;
    gap: 8px; flex-wrap: wrap; padding: 20px 0;
    font-family: 'JetBrains Mono', monospace; font-size: 11px; color: #64748b; }
.nx-footer .nx-pill { font-size: 10px; padding: 4px 10px; }
div[data-testid="stAlert"] { background: #0b1220 !important; border: 1px solid #1f2937 !important;
    border-radius: 10px !important; color: #cbd5e1 !important; }

.nx-feed { background: #0b1220; border: 1px solid #1f2937; border-radius: 12px;
    padding: 8px 4px; max-height: 340px; overflow-y: auto; }
.nx-event { display: flex; gap: 14px; padding: 10px 14px;
    border-bottom: 1px solid #111a2b; align-items: flex-start; }
.nx-event:last-child { border-bottom: none; }
.nx-event-time { font-family: 'JetBrains Mono', monospace; font-size: 11px;
    color: #64748b; min-width: 70px; padding-top: 2px; }
.nx-event-dot { width: 8px; height: 8px; border-radius: 50%;
    margin-top: 7px; flex-shrink: 0; background: #22d3ee;
    box-shadow: 0 0 6px currentColor; }
.nx-event-dot.crit { background: #ef4444; color: #ef4444; }
.nx-event-dot.warn { background: #f59e0b; color: #f59e0b; }
.nx-event-dot.ok   { background: #22c55e; color: #22c55e; }
.nx-event-dot.info { background: #22d3ee; color: #22d3ee; }
.nx-event-msg { color: #cbd5e1; font-size: 13px; flex: 1; }
</style>
"""


def inject_theme() -> None:
    """Inject the dashboard theme. Safe to call multiple times."""
    st.markdown(THEME_HTML, unsafe_allow_html=True)
