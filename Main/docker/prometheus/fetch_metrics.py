#!/usr/bin/env python3
"""
fetch_metrics_snapshot.py

Fragt alle konfigurierten Prometheus-Exporter-Endpoints ab, parst das
Prometheus-Exposition-Format in strukturiertes JSON (Name, Labels, Value, Typ,
Help-Text) und schreibt alles in ein einziges JSON-Dokument.

Dieses Format ist so gebaut, dass eine KI (oder ein Script) direkt daraus
Grafana-Panels ableiten kann, ohne den Rohtext selbst parsen zu müssen:
- jede Metrik ist ein eigenes Objekt mit name/labels/value/type/help
- "type" (counter/gauge/histogram/summary/untyped) steuert direkt, welche
  Panel-Art sinnvoll ist (z.B. counter -> rate() + Time Series,
  gauge -> aktueller Wert / Gauge-Panel)

Nutzung:
    python3 fetch_metrics_snapshot.py [--out DATEI.json] [--token-file PFAD]

Benötigt nur die Python-Standardbibliothek (kein pip install nötig).
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# job_name -> (URL, braucht Bearer-Token?)
TARGETS = {
    "nginx":            ("http://nginx_exporter:9113/metrics", False),
    "server-node":      ("http://192.168.178.88:9100/metrics", False),
    "server-ipmi":      ("http://192.168.178.88:9290/metrics", False),
    "server-smart":     ("http://192.168.178.88:9633/metrics", False),
    "server-process":   ("http://192.168.178.88:9256/metrics", False),
    "minecraft":        ("http://192.168.178.88:19565/metrics", False),
    "pi-node":          ("http://192.168.178.141:9100/metrics", False),
    "pi-smart":         ("http://192.168.178.141:9633/metrics", False),
    "homeassistant":    ("http://192.168.178.39:8123/api/prometheus", True),
}

DEFAULT_TOKEN_FILE = "/home/tobimax/production/MCDeploy/Main/docker/prometheus/secrets/ha_token"

# --- Prometheus-Exposition-Format-Parser -----------------------------------

METRIC_LINE_RE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)'
    r'(\{(?P<labels>.*)\})?'
    r'\s+(?P<value>[^\s]+)'
    r'(\s+(?P<timestamp>\d+))?$'
)

LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:[^"\\]|\\.)*)"')


def parse_labels(label_str):
    if not label_str:
        return {}
    labels = {}
    for m in LABEL_RE.finditer(label_str):
        val = m.group("value").replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        labels[m.group("key")] = val
    return labels


def parse_prometheus_text(text):
    """
    Parst Prometheus-Exposition-Format-Text in eine Liste von
    {name, labels, value, type, help} Objekten.
    """
    help_map = {}
    type_map = {}
    metrics = []

    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue

        if line.startswith("# HELP "):
            rest = line[len("# HELP "):]
            parts = rest.split(" ", 1)
            if len(parts) == 2:
                help_map[parts[0]] = parts[1]
            continue

        if line.startswith("# TYPE "):
            rest = line[len("# TYPE "):]
            parts = rest.split(" ", 1)
            if len(parts) == 2:
                type_map[parts[0]] = parts[1]
            continue

        if line.startswith("#"):
            continue  # sonstige Kommentare ignorieren

        m = METRIC_LINE_RE.match(line)
        if not m:
            continue  # Zeile nicht im erwarteten Format, überspringen

        name = m.group("name")
        labels = parse_labels(m.group("labels"))
        raw_value = m.group("value")

        try:
            if raw_value in ("+Inf", "Inf"):
                value = float("inf")
            elif raw_value == "-Inf":
                value = float("-inf")
            elif raw_value == "NaN":
                value = None  # NaN ist in JSON nicht valide
            else:
                value = float(raw_value)
        except ValueError:
            continue

        # Basisname ohne _bucket/_sum/_count-Suffix für Type/Help-Lookup
        base_name = name
        for suffix in ("_bucket", "_sum", "_count"):
            if base_name.endswith(suffix) and base_name[: -len(suffix)] in type_map:
                base_name = base_name[: -len(suffix)]
                break

        metrics.append({
            "name": name,
            "labels": labels,
            "value": value,
            "type": type_map.get(base_name, "untyped"),
            "help": help_map.get(base_name),
        })

    return metrics


# --- Fetch-Logik -------------------------------------------------------------

def fetch(url, token=None, timeout=10):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description="Prometheus-Exporter-Snapshot als strukturiertes JSON")
    parser.add_argument("--out", default=None, help="Ausgabedatei (Default: metrics_snapshot_<timestamp>.json)")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE, help="Pfad zum Home-Assistant Bearer-Token")
    args = parser.parse_args()

    out_path = args.out or f"metrics_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    ha_token = None
    try:
        with open(args.token_file, "r") as f:
            ha_token = f.read().strip()
    except OSError:
        ha_token = None

    result = {
        "snapshot_taken_at": datetime.now(timezone.utc).isoformat(),
        "targets": [],
    }

    for job_name, (url, needs_token) in TARGETS.items():
        entry = {
            "job_name": job_name,
            "target_url": url,
            "status": "ok",
            "error": None,
            "metric_count": 0,
            "metrics": [],
        }

        if needs_token and not ha_token:
            entry["status"] = "error"
            entry["error"] = f"Kein Bearer-Token gefunden unter {args.token_file}"
            result["targets"].append(entry)
            print(f"[FEHLER] {job_name}: kein Token", file=sys.stderr)
            continue

        try:
            text = fetch(url, token=ha_token if needs_token else None)
            metrics = parse_prometheus_text(text)
            entry["metrics"] = metrics
            entry["metric_count"] = len(metrics)
            print(f"[OK] {job_name}: {len(metrics)} Metriken", file=sys.stderr)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            entry["status"] = "error"
            entry["error"] = str(e)
            print(f"[FEHLER] {job_name}: {e}", file=sys.stderr)

        result["targets"].append(entry)

    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, allow_nan=False)

    print(f"Fertig: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
