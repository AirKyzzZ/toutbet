import collections
import json
import sys
from pathlib import Path
from typing import Any

import httpx

AGENT_TRACES = "http://127.0.0.1:8126/test/traces"


def load_traces(source: str) -> list[list[dict[str, Any]]]:
    if source.startswith("http"):
        traces: list[list[dict[str, Any]]] = httpx.get(source, timeout=300).json()
        return traces
    loaded: list[list[dict[str, Any]]] = json.loads(Path(source).read_text())
    return loaded


def iast_report(span: dict[str, Any]) -> dict[str, Any] | None:
    report = (span.get("meta_struct") or {}).get("iast")
    if report is None and "_dd.iast.json" in (span.get("meta") or {}):
        report = json.loads(span["meta"]["_dd.iast.json"])
    return report


def main() -> int:
    traces = load_traces(sys.argv[1] if len(sys.argv) > 1 else AGENT_TRACES)
    requests = 0
    enabled = 0
    metrics: collections.Counter[str] = collections.Counter()
    vulnerabilities: collections.Counter[tuple[Any, ...]] = collections.Counter()
    examples: dict[tuple[Any, ...], str] = {}
    for trace in traces:
        for span in trace:
            span_metrics = span.get("metrics") or {}
            if span.get("name") == "fastapi.request":
                requests += 1
                enabled += bool(span_metrics.get("_dd.iast.enabled"))
                metrics.update(
                    {k: v for k, v in span_metrics.items() if k.startswith("_dd.iast.telemetry")}
                )
            for vulnerability in (iast_report(span) or {}).get("vulnerabilities", []):
                location = vulnerability.get("location", {})
                key = (vulnerability["type"], location.get("path"), location.get("line"))
                vulnerabilities[key] += 1
                examples.setdefault(key, json.dumps(vulnerability.get("evidence"))[:200])
    print(f"requests traced: {requests}, with IAST enabled: {enabled}")
    for name, value in sorted(metrics.items()):
        print(f"  {name}: {value:g}")
    print(f"vulnerabilities: {sum(vulnerabilities.values())}")
    for key, count in vulnerabilities.most_common():
        print(f"  {count} {key} {examples[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
