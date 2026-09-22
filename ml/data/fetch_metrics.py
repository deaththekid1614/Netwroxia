#!/usr/bin/env python3
"""
Netwroxia Stage 3 — fetch_metrics.py
Pulls telemetry from InfluxDB 1.8 for ML training.
"""

import requests
import pandas as pd
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import os
import re

# ── CONFIG ──────────────────────────────────────────────────────────────────
INFLUXDB_URL = "http://localhost:8086"
DB_NAME = "netwroxia"
TIMEOUT = 30
DEFAULT_HOURS = 48

# URL → router mapping for ping metrics.
# Legacy keys (IPs) kept so historical data still resolves.
# New keys (hostnames) used after A6 telegraf.conf change.
IP_TO_ROUTER = {
    # Legacy (pre-A6)
    "172.20.20.3": "HO-Chennai",
    "172.20.20.4": "ZO-Bengaluru",
    "172.20.20.5": "BR-Koramangala",
    "172.20.20.7": "BR-Whitefield",
    # Current (post-A6)
    "clab-netwroxia-ho-chennai": "HO-Chennai",
    "clab-netwroxia-zo-bengaluru": "ZO-Bengaluru",
    "clab-netwroxia-br-koramangala": "BR-Koramangala",
    "clab-netwroxia-br-whitefield": "BR-Whitefield",
}

# Containerlab router container names → banking names
ROUTER_MAP = {
    "clab-netwroxia-ho-chennai": "HO-Chennai",
    "clab-netwroxia-zo-bengaluru": "ZO-Bengaluru",
    "clab-netwroxia-br-koramangala": "BR-Koramangala",
    "clab-netwroxia-br-whitefield": "BR-Whitefield",
}


def get_past_timestamp(hours: int) -> str:
    """Generate explicit UTC timestamp for InfluxDB queries."""
    past = datetime.utcnow() - timedelta(hours=hours)
    return past.strftime("%Y-%m-%dT%H:%M:%SZ")


def query_influxdb(query: str) -> Optional[Dict]:
    """Execute an InfluxQL query and return parsed JSON."""
    url = f"{INFLUXDB_URL}/query"
    params = {"db": DB_NAME, "q": query}
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] Query failed: {e}")
        return None


def parse_series(series: Dict) -> pd.DataFrame:
    """Convert InfluxDB series response to DataFrame."""
    if not series or "values" not in series:
        return pd.DataFrame()

    columns = series.get("columns", [])
    values = series["values"]

    df = pd.DataFrame(values, columns=columns)
    if "time" in df.columns:
        # FIX: Old pandas can't parse nanosecond ISO timestamps with Z suffix.
        # Use Python's built-in parser which handles any precision, then wrap in pd.Timestamp.
        def _safe_parse(ts):
            ts = str(ts).replace(" ", "T")  # Normalize space to T
            if ts.endswith("Z"):
                ts = ts[:-1]  # Strip Z — Python 3.8 fromisoformat doesn't handle Z
            # Truncate fractional seconds to max 6 digits (microseconds)
            ts = re.sub(r'(\.\d{6})\d*', r'\1', ts)
            return pd.Timestamp(datetime.fromisoformat(ts), tz='UTC')
        
        df["time"] = df["time"].apply(_safe_parse)
    return df


def fetch_ping(hours: int = DEFAULT_HOURS) -> pd.DataFrame:
    """Fetch ping metrics (path health)."""
    since = get_past_timestamp(hours)
    query = f'''
        SELECT time, url, average_response_ms, percent_packet_loss, packets_transmitted, packets_received
        FROM ping
        WHERE time > '{since}'
    '''
    data = query_influxdb(query)
    if not data or "results" not in data:
        return pd.DataFrame()

    series = data["results"][0].get("series", [])
    if not series:
        return pd.DataFrame()

    df = parse_series(series[0])
    if not df.empty and "url" in df.columns:
        df["router"] = df["url"].map(IP_TO_ROUTER)
        df["metric_type"] = "path_health"
    return df


def fetch_ospf(hours: int = DEFAULT_HOURS) -> pd.DataFrame:
    """Fetch OSPF neighbor counts."""
    since = get_past_timestamp(hours)
    query = f'''
        SELECT time, router, count
        FROM ospf_neighbors
        WHERE time > '{since}'
    '''
    data = query_influxdb(query)
    if not data or "results" not in data:
        return pd.DataFrame()

    series = data["results"][0].get("series", [])
    if not series:
        return pd.DataFrame()

    df = parse_series(series[0])
    if not df.empty:
        df["metric_type"] = "routing"
    return df


def fetch_bgp(hours: int = DEFAULT_HOURS) -> pd.DataFrame:
    """Fetch BGP peer states."""
    since = get_past_timestamp(hours)
    query = f'''
        SELECT time, router, peer, state
        FROM bgp_peer
        WHERE time > '{since}'
    '''
    data = query_influxdb(query)
    if not data or "results" not in data:
        return pd.DataFrame()

    series = data["results"][0].get("series", [])
    if not series:
        return pd.DataFrame()

    df = parse_series(series[0])
    if not df.empty:
        df["metric_type"] = "routing"
        df["state_ok"] = (df["state"].astype(int) == 1).astype(int)
    return df


def fetch_container_cpu(hours: int = DEFAULT_HOURS) -> pd.DataFrame:
    """Fetch Docker container CPU usage."""
    since = get_past_timestamp(hours)
    query = f'''
        SELECT time, container_name, usage_percent
        FROM docker_container_cpu
        WHERE time > '{since}'
    '''
    data = query_influxdb(query)
    if not data or "results" not in data:
        return pd.DataFrame()

    series = data["results"][0].get("series", [])
    if not series:
        return pd.DataFrame()

    df = parse_series(series[0])
    if not df.empty:
        df["metric_type"] = "container_resource"
        df["router"] = df["container_name"].map(ROUTER_MAP)
    return df


def fetch_container_mem(hours: int = DEFAULT_HOURS) -> pd.DataFrame:
    """Fetch Docker container memory usage."""
    since = get_past_timestamp(hours)
    query = f'''
        SELECT time, container_name, usage_percent
        FROM docker_container_mem
        WHERE time > '{since}'
    '''
    data = query_influxdb(query)
    if not data or "results" not in data:
        return pd.DataFrame()

    series = data["results"][0].get("series", [])
    if not series:
        return pd.DataFrame()

    df = parse_series(series[0])
    if not df.empty:
        df["metric_type"] = "container_resource"
        df["router"] = df["container_name"].map(ROUTER_MAP)
    return df


def fetch_all_metrics(hours: int = DEFAULT_HOURS) -> Dict[str, pd.DataFrame]:
    """Fetch all metrics and return as dict of DataFrames."""
    since = get_past_timestamp(hours)
    print(f"[INFO] Fetching metrics from InfluxDB...")
    print(f"[INFO] Time range: {since} → now (UTC)")
    print(f"[INFO] InfluxDB: {INFLUXDB_URL} | DB: {DB_NAME}")

    results = {
        "ping": fetch_ping(hours),
        "ospf_neighbors": fetch_ospf(hours),
        "bgp_peer": fetch_bgp(hours),
        "docker_container_cpu": fetch_container_cpu(hours),
        "docker_container_mem": fetch_container_mem(hours),
    }

    total_rows = sum(len(df) for df in results.values())
    print(f"[INFO] Total rows fetched: {total_rows}")

    for name, df in results.items():
        if df.empty:
            print(f"[WARN] {name}: NO DATA")
        else:
            print(f"[OK]   {name}: {len(df)} rows, cols: {list(df.columns)}")

    return results


def save_raw_data(results: Dict[str, pd.DataFrame], out_dir: str = "ml/data/raw"):
    """Save fetched data to CSV for inspection."""
    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for name, df in results.items():
        if not df.empty:
            path = os.path.join(out_dir, f"{name}_{timestamp}.csv")
            df.to_csv(path, index=False)
            print(f"[SAVE] {path} ({len(df)} rows)")


# ── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    hours = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_HOURS
    results = fetch_all_metrics(hours)

    # Quick sanity check
    print("\n" + "=" * 60)
    print("SANITY CHECK")
    print("=" * 60)

    if not results["ping"].empty:
        ping = results["ping"]
        print(f"\n[PING] Latest 3 samples:")
        print(ping.tail(3).to_string())
        print(f"\n[PING] Stats: mean={ping['average_response_ms'].mean():.2f}ms, "
              f"max_loss={ping['percent_packet_loss'].max():.1f}%")

    if not results["ospf_neighbors"].empty:
        ospf = results["ospf_neighbors"]
        print(f"\n[OSPF] Latest 3 samples:")
        print(ospf.tail(3).to_string())

    if not results["bgp_peer"].empty:
        bgp = results["bgp_peer"]
        print(f"\n[BGP] Latest 3 samples:")
        print(bgp.tail(3).to_string())

    # Save
    save_raw_data(results)
    print("\n[INFO] Done. Raw data saved to ml/data/raw/")