"""Machine-readable (JSON) and human-readable (Markdown) evaluation reports."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2] / "reports" / "evaluation"

LIMITATIONS = [
    "Labels are the present-day payee and category assignments in the history; they may differ from what was "
    "chosen at the time, and renamed categories appear under their current names.",
    "Accuracy is measured on this history only and is not an expected rate for future imports.",
    "Descriptions come from the YNAB memo, which can contain user notes instead of bank text.",
]
HINDSIGHT = ("Rules written with hindsight: they were authored with knowledge of the evaluation period, "
             "so treat these results as optimistic.")


def git_commit() -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).parent, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def write(report: dict, out_dir: Path = DEFAULT_OUT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = out_dir / f"{stamp}-{report['config']['input_sha256'][:8]}"
    base.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    base.with_suffix(".md").write_text(markdown(report))
    return base.with_suffix(".json")


def _pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _table(title: str, groups: dict) -> list:
    lines = [f"**{title}**", "", "| | rows | accuracy | coverage |", "|---|---:|---:|---:|"]
    lines += [f"| {k} | {v['rows']} | {_pct(v['accuracy'])} | {_pct(v['coverage'])} |" for k, v in groups.items()]
    return lines + [""]


def markdown(report: dict) -> str:
    cfg, split = report["config"], report["split"]
    out = [
        "# Classification evaluation", "",
        f"Input: `{cfg['input']}` (sha256 {cfg['input_sha256'][:12]}…), code {cfg.get('git_commit') or 'unknown'}.",
        f"Training {split['training']['from']} → {split['training']['to']} ({split['training']['rows']} rows); "
        f"evaluation {split['evaluation']['from']} → {split['evaluation']['to']} ({split['evaluation']['rows']} rows); "
        f"cut-off {split['cutoff']}.", "",
        "## Limitations", "", *[f"- {x}" for x in report["limitations"]], "",
        "## Input treatment", "", "| treatment | rows |", "|---|---:|",
        *[f"| {k} | {v} |" for k, v in report["treatments"].items()],
        f"| *total* | {sum(report['treatments'].values())} |", "",
        f"Duplicate rows kept and evaluated individually: {report['duplicates']}.", "",
    ]
    for name, variant in report["variants"].items():
        out += [f"## Variant: {name}", ""]
        if variant.get("note"):
            out += [f"> {variant['note']}", ""]
        rows = variant["metrics"]["rows"]
        out += [f"- Rows evaluated: {rows['rows']} (payee-only transfers: {rows['payee_only_rows']})",
                f"- Would auto-accept: {rows['would_auto_accept']}, precision {_pct(rows['auto_accept_precision'])}",
                f"- Rows needing a correction: {rows['corrections_required']}", ""]
        for target in ("payee", "category"):
            m = variant["metrics"][target]
            out += [f"### {target.capitalize()}", "",
                    f"Accuracy {_pct(m['accuracy'])} on {m['rows']} rows; coverage {_pct(m['coverage'])}; "
                    f"unclassified {m['unclassified']}.", ""]
            out += _table("By confidence band", m["by_band"])
            out += _table("By source", m["by_source"])
            out += _table("By account", m["by_account"])
            out += _table("Subsets", m["subsets"])
            out += ["**Most confident mistakes**", "", "| conf | source | description | predicted | actual |",
                    "|---:|---|---|---|---|"]
            out += [f"| {f['confidence']:.2f} | {f['source']} | {f['description'][:60]} | {f['predicted']} | {f['actual']} |"
                    for f in m["confident_failures"][:10]]
            out += [""]
    return "\n".join(out) + "\n"
