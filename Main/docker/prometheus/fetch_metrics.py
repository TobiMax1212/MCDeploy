#!/usr/bin/env python3
"""
fetch_metrics_snapshot.py

Erstellt einen Snapshot aller Datenquellen, aus dem eine KI ein Grafana-
Dashboard (Schema v2, TabsLayout) bauen kann:

1. Prometheus-Exporter
   Alle konfigurierten /metrics-Endpoints werden abgefragt und in
   strukturiertes JSON geparst (name/labels/value/type/help). Zusätzlich gibt
   es pro Target eine "families"-Zusammenfassung (eine Zeile pro Metrik-
   Familie mit Typ, Label-Keys, Beispiel-Labelwerten, Serienanzahl), damit
   die KI nicht tausende Einzelsamples durchgehen muss.

2. Loki
   Über die Loki-HTTP-API werden ermittelt:
   - Labels und deren Werte (Kardinalität)
   - alle Log-Streams (Label-Kombinationen)
   - pro Stream: Zeilenanzahl und Fehlerzeilen im Lookback-Fenster,
     Beispielzeilen, erkanntes Format (json/logfmt/plain), erkannte Log-
     Level und Feldnamen (für | json / | logfmt Parser in LogQL)
   - Volumen aggregiert pro Label (für Bar/Pie-Panels)

Nutzung:
    python3 fetch_metrics_snapshot.py [--out DATEI.json]
        [--token-file PFAD]
        [--loki-url http://192.168.178.141:3100] [--loki-lookback 24h]
        [--loki-sample-lines 5] [--loki-max-streams 50] [--no-loki]
        [--prom-ds-uid UID] [--loki-ds-uid UID] [--no-raw]

Benötigt nur die Python-Standardbibliothek (kein pip install nötig).
"""

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# job_name -> (URL, braucht Bearer-Token?)
# job_name sollte dem `job`-Label in Prometheus entsprechen, die KI baut
# ihre PromQL-Queries mit {job="<job_name>"}.
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
    # Lokis eigene Metriken – nur einkommentieren, wenn Prometheus Loki
    # auch unter diesem job-Namen scrapt, sonst baut die KI leere Panels.
    # "loki":           ("http://192.168.178.141:3100/metrics", False),
}

DEFAULT_TOKEN_FILE = "/home/tobimax/production/MCDeploy/Main/docker/prometheus/secrets/ha_token"
DEFAULT_LOKI_URL = "http://192.168.178.141:3100"

# Regex für Fehlerzeilen (LogQL-Line-Filter, RE2-kompatibel)
LOKI_ERROR_REGEX = r"(?i)(error|fatal|panic|exception|crit|failed)"

# --- Allgemeine Helfer -------------------------------------------------------


def http_get(url, token=None, timeout=10):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def json_safe_number(v):
    """Inf/NaN sind in JSON nicht erlaubt -> als String bzw. null ablegen."""
    if v is None:
        return None
    if math.isnan(v):
        return None
    if math.isinf(v):
        return "+Inf" if v > 0 else "-Inf"
    return v


def parse_duration(s):
    """'24h', '30m', '7d', '90s' -> Sekunden."""
    m = re.fullmatch(r"(\d+)([smhdw])", s.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"Ungültige Dauer: {s} (z.B. 24h, 30m, 7d)")
    factor = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
    return int(m.group(1)) * factor


# --- Prometheus-Exposition-Format-Parser -----------------------------------

METRIC_LINE_RE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)'
    r'(\{(?P<labels>.*)\})?'
    r'\s+(?P<value>[^\s]+)'
    r'(\s+(?P<timestamp>-?\d+))?$'
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
    {name, base_name, labels, value, type, help} Objekten.
    """
    help_map = {}
    type_map = {}
    metrics = []

    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue

        if line.startswith("# HELP "):
            parts = line[len("# HELP "):].split(" ", 1)
            if len(parts) == 2:
                help_map[parts[0]] = parts[1]
            continue

        if line.startswith("# TYPE "):
            parts = line[len("# TYPE "):].split(" ", 1)
            if len(parts) == 2:
                type_map[parts[0]] = parts[1].strip()
            continue

        if line.startswith("#"):
            continue

        m = METRIC_LINE_RE.match(line)
        if not m:
            continue

        name = m.group("name")
        labels = parse_labels(m.group("labels"))
        raw_value = m.group("value")

        try:
            if raw_value in ("+Inf", "Inf"):
                value = float("inf")
            elif raw_value == "-Inf":
                value = float("-inf")
            elif raw_value == "NaN":
                value = float("nan")
            else:
                value = float(raw_value)
        except ValueError:
            continue

        # Basisname ohne _bucket/_sum/_count/_total-Suffix für Type/Help-Lookup
        base_name = name
        for suffix in ("_bucket", "_sum", "_count", "_total", "_created"):
            if base_name.endswith(suffix) and base_name[: -len(suffix)] in type_map:
                base_name = base_name[: -len(suffix)]
                break

        metrics.append({
            "name": name,
            "base_name": base_name,
            "labels": labels,
            "value": json_safe_number(value),
            "type": type_map.get(base_name, "untyped"),
            "help": help_map.get(base_name),
        })

    return metrics


def summarize_families(metrics, max_label_values=10):
    """
    Fasst Samples zu Metrik-Familien zusammen. Das ist die Hauptquelle für die
    KI: kompakt, aber mit allem, was zur Panel-Auswahl nötig ist.
    """
    fams = {}
    for s in metrics:
        f = fams.setdefault(s["base_name"], {
            "name": s["base_name"],
            "type": s["type"],
            "help": s["help"],
            "sample_names": set(),
            "series_count": 0,
            "label_values": {},
            "min": None,
            "max": None,
        })
        f["sample_names"].add(s["name"])
        f["series_count"] += 1
        for k, v in s["labels"].items():
            vals = f["label_values"].setdefault(k, set())
            if len(vals) < max_label_values:
                vals.add(v)
        v = s["value"]
        if isinstance(v, (int, float)) and not s["name"].endswith("_bucket"):
            f["min"] = v if f["min"] is None else min(f["min"], v)
            f["max"] = v if f["max"] is None else max(f["max"], v)

    out = []
    for f in sorted(fams.values(), key=lambda x: x["name"]):
        out.append({
            "name": f["name"],
            "type": f["type"],
            "help": f["help"],
            "sample_names": sorted(f["sample_names"]),
            "series_count": f["series_count"],
            "label_keys": sorted(f["label_values"].keys()),
            "label_values_sample": {k: sorted(v) for k, v in sorted(f["label_values"].items())},
            "value_min": f["min"],
            "value_max": f["max"],
        })
    return out


# --- Loki --------------------------------------------------------------------

LEVEL_RE = re.compile(
    r'(?i)(?:\blevel[=:]\s*"?|\blvl[=:]\s*"?|\[|\b)'
    r'(trace|debug|info|warn(?:ing)?|error|err|fatal|crit(?:ical)?|panic)\b'
)
LOGFMT_RE = re.compile(r'\b[a-zA-Z_][a-zA-Z0-9_.]*=("[^"]*"|\S+)')


class LokiClient:
    def __init__(self, base_url, timeout=20):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def get_json(self, path, params=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.loads(http_get(url, timeout=self.timeout))
        if isinstance(data, dict) and data.get("status") not in (None, "success"):
            raise RuntimeError(f"Loki-Fehler bei {path}: {data}")
        return data

    def get_text(self, path):
        return http_get(self.base + path, timeout=self.timeout).strip()


def logql_escape(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


def stream_selector(labels):
    inner = ",".join(f'{k}="{logql_escape(v)}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}"


def analyze_lines(lines):
    """Erkennt Format, Level-Verteilung und Feldnamen anhand von Beispielzeilen."""
    formats = {"json": 0, "logfmt": 0, "plain": 0}
    levels = {}
    json_keys = set()
    logfmt_keys = set()

    for line in lines:
        stripped = line.strip()
        parsed = None
        if stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
        if isinstance(parsed, dict):
            formats["json"] += 1
            json_keys.update(parsed.keys())
            lvl = parsed.get("level") or parsed.get("lvl") or parsed.get("severity")
            if isinstance(lvl, str):
                key = lvl.lower()
                levels[key] = levels.get(key, 0) + 1
                continue
        else:
            pairs = LOGFMT_RE.findall(stripped)
            if len(pairs) >= 2 and "=" in stripped.split(" ", 1)[0]:
                formats["logfmt"] += 1
                logfmt_keys.update(
                    m.group(0).split("=", 1)[0] for m in LOGFMT_RE.finditer(stripped)
                )
            else:
                formats["plain"] += 1

        m = LEVEL_RE.search(stripped)
        if m:
            key = m.group(1).lower()
            key = {"warning": "warn", "err": "error", "critical": "crit"}.get(key, key)
            levels[key] = levels.get(key, 0) + 1

    dominant = max(formats, key=formats.get) if lines else None
    return {
        "format_counts": formats,
        "dominant_format": dominant,
        "levels_in_sample": levels,
        "json_keys": sorted(json_keys)[:50],
        "logfmt_keys": sorted(logfmt_keys)[:50],
    }


def instant_sum(loki, query, now_ns):
    data = loki.get_json("/loki/api/v1/query", {"query": query, "time": now_ns})
    result = data.get("data", {}).get("result", [])
    return result


def fetch_loki(base_url, lookback_s, sample_lines, max_streams, max_label_values=100):
    loki = LokiClient(base_url)
    now_ns = int(time.time() * 1e9)
    start_ns = now_ns - lookback_s * 1_000_000_000
    lb = f"{lookback_s}s"

    out = {
        "url": base_url,
        "status": "ok",
        "errors": [],
        "version": None,
        "lookback_seconds": lookback_s,
        "labels": [],
        "streams_total": 0,
        "streams_truncated": False,
        "streams": [],
        "volume_by_label": {},
        "error_lines_by_label": {},
        "error_regex_used": LOKI_ERROR_REGEX,
    }

    def err(step, e):
        out["errors"].append({"step": step, "error": str(e)})
        print(f"[LOKI-WARN] {step}: {e}", file=sys.stderr)

    # Erreichbarkeit
    try:
        ready = loki.get_text("/ready")
        if ready.lower() != "ready":
            err("ready", f"Antwort: {ready}")
    except Exception as e:  # noqa: BLE001
        out["status"] = "error"
        err("ready", e)
        return out

    try:
        bi = loki.get_json("/loki/api/v1/status/buildinfo")
        out["version"] = bi.get("version")
    except Exception as e:  # noqa: BLE001
        err("buildinfo", e)

    # Labels + Werte
    try:
        label_names = loki.get_json("/loki/api/v1/labels",
                                    {"start": start_ns, "end": now_ns}).get("data", []) or []
    except Exception as e:  # noqa: BLE001
        out["status"] = "error"
        err("labels", e)
        return out

    for name in sorted(label_names):
        if name.startswith("__"):
            continue
        entry = {"name": name, "value_count": 0, "values": [], "values_truncated": False}
        try:
            vals = loki.get_json(f"/loki/api/v1/label/{urllib.parse.quote(name)}/values",
                                 {"start": start_ns, "end": now_ns}).get("data", []) or []
            entry["value_count"] = len(vals)
            entry["values"] = sorted(vals)[:max_label_values]
            entry["values_truncated"] = len(vals) > max_label_values
        except Exception as e:  # noqa: BLE001
            err(f"label_values:{name}", e)
        out["labels"].append(entry)

    # Gute Kandidaten für "sum by (...)": mehr als 1, höchstens 50 Werte
    out["groupable_labels"] = [
        l["name"] for l in sorted(out["labels"], key=lambda x: x["value_count"])
        if 1 < l["value_count"] <= 50
    ]

    # Streams (Label-Kombinationen) einsammeln
    streams = {}
    for l in out["labels"]:
        try:
            data = loki.get_json("/loki/api/v1/series", {
                "match[]": f'{{{l["name"]}=~".+"}}',
                "start": start_ns, "end": now_ns,
            }).get("data", []) or []
            for s in data:
                key = tuple(sorted(s.items()))
                streams[key] = s
        except Exception as e:  # noqa: BLE001
            err(f"series:{l['name']}", e)

    out["streams_total"] = len(streams)
    stream_list = sorted(streams.values(), key=lambda s: stream_selector(s))
    if len(stream_list) > max_streams:
        out["streams_truncated"] = True
        stream_list = stream_list[:max_streams]

    # Pro Stream: Volumen, Fehlerzeilen, Beispiele, Format
    for labels in stream_list:
        sel = stream_selector(labels)
        s_entry = {
            "selector": sel,
            "labels": labels,
            "lines_in_lookback": None,
            "error_lines_in_lookback": None,
            "sample_lines": [],
            "analysis": None,
        }
        try:
            r = instant_sum(loki, f"sum(count_over_time({sel}[{lb}]))", now_ns)
            s_entry["lines_in_lookback"] = int(float(r[0]["value"][1])) if r else 0
        except Exception as e:  # noqa: BLE001
            err(f"count:{sel}", e)
        try:
            r = instant_sum(loki, f'sum(count_over_time({sel} |~ `{LOKI_ERROR_REGEX}` [{lb}]))', now_ns)
            s_entry["error_lines_in_lookback"] = int(float(r[0]["value"][1])) if r else 0
        except Exception as e:  # noqa: BLE001
            err(f"errors:{sel}", e)
        if sample_lines > 0:
            try:
                data = loki.get_json("/loki/api/v1/query_range", {
                    "query": sel, "start": start_ns, "end": now_ns,
                    "limit": sample_lines, "direction": "backward",
                })
                lines = []
                for res in data.get("data", {}).get("result", []):
                    for _ts, line in res.get("values", []):
                        lines.append(line)
                lines = lines[:sample_lines]
                s_entry["sample_lines"] = [ln[:500] for ln in lines]
                s_entry["analysis"] = analyze_lines(lines)
            except Exception as e:  # noqa: BLE001
                err(f"samples:{sel}", e)
        out["streams"].append(s_entry)

    # Volumen + Fehler aggregiert pro groupable Label
    for name in out["groupable_labels"]:
        try:
            r = instant_sum(loki, f'sum by ({name}) (count_over_time({{{name}=~".+"}}[{lb}]))', now_ns)
            out["volume_by_label"][name] = {
                x["metric"].get(name, ""): int(float(x["value"][1])) for x in r
            }
        except Exception as e:  # noqa: BLE001
            err(f"volume_by:{name}", e)
        try:
            r = instant_sum(
                loki,
                f'sum by ({name}) (count_over_time({{{name}=~".+"}} |~ `{LOKI_ERROR_REGEX}` [{lb}]))',
                now_ns,
            )
            out["error_lines_by_label"][name] = {
                x["metric"].get(name, ""): int(float(x["value"][1])) for x in r
            }
        except Exception as e:  # noqa: BLE001
            err(f"errors_by:{name}", e)

    if out["errors"] and out["status"] == "ok":
        out["status"] = "partial"
    return out


# --- Main --------------------------------------------------------------------

def fetch_prometheus_targets(ha_token, token_file, include_raw):
    targets = []
    for job_name, (url, needs_token) in TARGETS.items():
        entry = {
            "job_name": job_name,
            "target_url": url,
            "status": "ok",
            "error": None,
            "metric_count": 0,
            "family_count": 0,
            "families": [],
        }
        if include_raw:
            entry["metrics"] = []

        if needs_token and not ha_token:
            entry["status"] = "error"
            entry["error"] = f"Kein Bearer-Token gefunden unter {token_file}"
            targets.append(entry)
            print(f"[FEHLER] {job_name}: kein Token", file=sys.stderr)
            continue

        try:
            text = http_get(url, token=ha_token if needs_token else None)
            metrics = parse_prometheus_text(text)
            entry["families"] = summarize_families(metrics)
            entry["metric_count"] = len(metrics)
            entry["family_count"] = len(entry["families"])
            if include_raw:
                for m in metrics:
                    m.pop("base_name", None)
                entry["metrics"] = metrics
            print(f"[OK] {job_name}: {len(metrics)} Samples, "
                  f"{entry['family_count']} Familien", file=sys.stderr)
        except Exception as e:  # noqa: BLE001  (URLError, Timeout, ConnectionReset, ...)
            entry["status"] = "error"
            entry["error"] = str(e)
            print(f"[FEHLER] {job_name}: {e}", file=sys.stderr)

        targets.append(entry)
    return targets


def main():
    p = argparse.ArgumentParser(description="Prometheus- + Loki-Snapshot als strukturiertes JSON")
    p.add_argument("--out", default=None, help="Ausgabedatei (Default: metrics_snapshot_<timestamp>.json)")
    p.add_argument("--token-file", default=DEFAULT_TOKEN_FILE, help="Pfad zum Home-Assistant Bearer-Token")
    p.add_argument("--no-raw", action="store_true",
                   help="Einzelsamples weglassen, nur Familien-Zusammenfassung (viel kleinere Datei)")
    p.add_argument("--loki-url", default=DEFAULT_LOKI_URL)
    p.add_argument("--loki-lookback", default="24h", type=parse_duration,
                   help="Zeitfenster für Loki-Auswertung (z.B. 6h, 24h, 7d)")
    p.add_argument("--loki-sample-lines", default=5, type=int, help="Beispielzeilen pro Stream")
    p.add_argument("--loki-max-streams", default=50, type=int, help="Max. Streams, die im Detail analysiert werden")
    p.add_argument("--no-loki", action="store_true", help="Loki überspringen")
    p.add_argument("--prom-ds-uid", default="prometheus", help="UID der Prometheus-Datasource in Grafana")
    p.add_argument("--loki-ds-uid", default="loki", help="UID der Loki-Datasource in Grafana")
    args = p.parse_args()

    out_path = args.out or f"metrics_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    ha_token = None
    try:
        with open(args.token_file, "r") as f:
            ha_token = f.read().strip() or None
    except OSError:
        ha_token = None

    result = {
        "snapshot_format_version": 2,
        "snapshot_taken_at": datetime.now(timezone.utc).isoformat(),
        "datasources": {
            "prometheus": {"type": "prometheus", "uid": args.prom_ds_uid},
            "loki": {"type": "loki", "uid": args.loki_ds_uid, "url": args.loki_url},
        },
        "targets": fetch_prometheus_targets(ha_token, args.token_file, include_raw=not args.no_raw),
        "loki": None,
    }

    if not args.no_loki:
        print(f"[LOKI] frage {args.loki_url} ab (Lookback {args.loki_lookback}s) ...", file=sys.stderr)
        result["loki"] = fetch_loki(args.loki_url, args.loki_lookback,
                                    args.loki_sample_lines, args.loki_max_streams)
        lk = result["loki"]
        print(f"[LOKI] Status {lk['status']}: {len(lk['labels'])} Labels, "
              f"{lk['streams_total']} Streams, {len(lk['errors'])} Warnungen", file=sys.stderr)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, allow_nan=False)

    print(f"Fertig: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
