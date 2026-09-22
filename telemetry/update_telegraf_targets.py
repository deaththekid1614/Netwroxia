#!/usr/bin/env python3
"""
Netwroxia — Phase A6: Telegraf Ping Target Updater

Regenerates the [inputs.ping] urls block in telegraf.conf from the
current containerlab container IPs. Run after every `containerlab deploy`
to keep telegraf's ping targets in sync.

Design:
  - Reads each container's IP from `docker inspect` (`.clab` network)
  - Backs up original telegraf.conf to telegraf.conf.bak before first edit
  - Rewrites only the urls = [...] block inside [inputs.ping]
  - Refuses to write if the block cannot be located
  - Restarts telegraf (unless --no-restart)

Usage:
  python3 telemetry/update_telegraf_targets.py
  python3 telemetry/update_telegraf_targets.py --dry-run
  python3 telemetry/update_telegraf_targets.py --no-restart
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# ── CONFIG ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TELEGRAF_CONF = PROJECT_ROOT / "telemetry" / "telegraf" / "telegraf.conf"
TELEGRAF_BAK = TELEGRAF_CONF.with_suffix(".conf.bak")

CLAB_NETWORK = "clab"

# Display name -> containerlab container name
ROUTERS: Dict[str, str] = {
    "HO-Chennai":     "clab-netwroxia-ho-chennai",
    "ZO-Bengaluru":   "clab-netwroxia-zo-bengaluru",
    "BR-Koramangala": "clab-netwroxia-br-koramangala",
    "BR-Whitefield":  "clab-netwroxia-br-whitefield",
}

EXEC_TIMEOUT = 15


# ── DOCKER HELPERS ──────────────────────────────────────────────────────────
def get_container_ip(container: str) -> str:
    """Return IPv4 on the clab network, or empty string if unavailable."""
    fmt = "{{.NetworkSettings.Networks.clab.IPAddress}}"
    try:
        result = subprocess.run(
            ["docker", "inspect", container, "--format", fmt],
            capture_output=True, text=True, timeout=EXEC_TIMEOUT,
        )
    except Exception as e:
        print(f"[ERROR] docker inspect failed for {container}: {e}")
        return ""
    if result.returncode != 0:
        print(f"[ERROR] container not found: {container}")
        return ""
    ip = (result.stdout or "").strip()
    if not ip:
        print(f"[ERROR] no '{CLAB_NETWORK}' network IP for {container}")
        return ""
    return ip


def fetch_all_ips() -> List[Tuple[str, str, str]]:
    """
    Returns a list of (display_name, container_name, ip).
    Skips routers whose containers are missing.
    """
    rows: List[Tuple[str, str, str]] = []
    for display, container in ROUTERS.items():
        ip = get_container_ip(container)
        if ip:
            rows.append((display, container, ip))
        else:
            print(f"[WARN] skipping {display} — no IP available")
    return rows


# ── TELEGRAF.CONF EDITOR ────────────────────────────────────────────────────
PING_BLOCK_RE = re.compile(
    r"(\[\[inputs\.ping\]\]\s*\n\s*urls\s*=\s*\[)(.*?)(\])",
    re.DOTALL,
)


def read_conf() -> str:
    if not TELEGRAF_CONF.exists():
        print(f"[FATAL] telegraf.conf not found: {TELEGRAF_CONF}")
        sys.exit(1)
    return TELEGRAF_CONF.read_text()


def build_new_urls_block(rows: List[Tuple[str, str, str]]) -> str:
    """Build the inner contents of the urls = [...] array."""
    lines = [""]
    for display, _, ip in rows:
        lines.append(f'    "{ip}",   # {display}')
    # Strip trailing comma from last entry
    if len(lines) > 1:
        lines[-1] = lines[-1].replace(",", "")
    lines.append("  ")
    return "\n".join(lines)


def rewrite_conf(conf_text: str, rows: List[Tuple[str, str, str]]) -> str:
    """Replace the urls array inside [inputs.ping]. Fails if block not found."""
    new_inner = build_new_urls_block(rows)

    def _replace(match: re.Match) -> str:
        prefix = match.group(1)   # "[[inputs.ping]]\n  urls = ["
        suffix = match.group(3)   # "]"
        return f"{prefix}{new_inner}{suffix}"

    if not PING_BLOCK_RE.search(conf_text):
        print("[FATAL] Could not locate [[inputs.ping]] urls = [...] block")
        print("[HINT]  telegraf.conf may have been edited. Restore from .bak")
        sys.exit(1)

    return PING_BLOCK_RE.sub(_replace, conf_text, count=1)


def backup_once() -> None:
    if TELEGRAF_BAK.exists():
        print(f"[INFO] Backup already exists: {TELEGRAF_BAK.name}")
        return
    shutil.copy2(TELEGRAF_CONF, TELEGRAF_BAK)
    print(f"[INFO] Backed up original -> {TELEGRAF_BAK.name}")


# ── TELEGRAF RESTART ────────────────────────────────────────────────────────
def restart_telegraf() -> bool:
    try:
        result = subprocess.run(
            ["sudo", "docker-compose", "restart", "telegraf"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        print(f"[ERROR] restart failed: {e}")
        return False
    if result.returncode != 0:
        print(f"[ERROR] restart returned {result.returncode}")
        print((result.stderr or result.stdout or "").strip()[:300])
        return False
    print("[OK]   telegraf restarted")
    return True


# ── MAIN ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netwroxia Telegraf Ping Target Updater (A6)"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show new config without writing or restarting")
    parser.add_argument("--no-restart", action="store_true",
                        help="Write config but do not restart telegraf")
    args = parser.parse_args()

    print("=" * 72)
    print(" NETWROXIA TELEGRAF PING TARGET UPDATER (A6)")
    print("=" * 72)
    print(f" Config : {TELEGRAF_CONF.relative_to(PROJECT_ROOT)}")
    print(f" Mode   : {'DRY-RUN' if args.dry_run else 'WRITE'}")
    print()

    rows = fetch_all_ips()
    if not rows:
        print("[FATAL] No container IPs found. Is the topology deployed?")
        sys.exit(1)

    print("[INFO] Current container IPs:")
    for display, container, ip in rows:
        print(f"  {display:18s} {ip:18s} ({container})")
    print()

    old_conf = read_conf()
    new_conf = rewrite_conf(old_conf, rows)

    if old_conf == new_conf and not args.dry_run:
        print("[INFO] telegraf.conf already up to date. Nothing to write.")
        if not args.no_restart:
            restart_telegraf()
        return

    if args.dry_run:
        print("[DRY-RUN] Would write the following [[inputs.ping]] block:")
        print("-" * 72)
        for line in new_conf.splitlines():
            if "inputs.ping" in line or "urls" in line or "# HO" in line \
               or "# ZO" in line or "# BR" in line or line.strip().startswith('"172.'):
                print(f"  {line}")
        print("-" * 72)
        print("[DRY-RUN] No changes written. No restart.")
        return

    backup_once()
    TELEGRAF_CONF.write_text(new_conf)
    print(f"[OK]   telegraf.conf updated ({len(rows)} ping targets)")

    if not args.no_restart:
        restart_telegraf()
        print()
        print("[SUGGEST] Verify with:")
        print("  curl -G 'http://localhost:8086/query?db=netwroxia' \\")
        print("    --data-urlencode 'q=SELECT * FROM ping ORDER BY time DESC LIMIT 5'")
    else:
        print("[INFO] --no-restart specified. Restart telegraf manually:")
        print("  sudo docker-compose restart telegraf")


if __name__ == "__main__":
    main()
