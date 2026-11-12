"""Full-coverage offline audit of an exported SFT dataset: structure, citations,
internal artifacts, duplicates, split integrity and derivation-aware numeric screen.

Usage: python export/audit_numeric.py --export-dir DIR --workspaces WS [WS ...]
Writes final_audit.json into the export directory.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "frozen" / "pipeline"), str(REPO / "pipeline"), str(REPO)]

from export_sft import ARTIFACTS, load, resolve_effective  # noqa: E402

CITE = re.compile(r"biorxiv_[A-Za-z0-9_]+:b\d+")
NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def nums(t):
    return [float(m.group(0).replace(",", "")) for m in NUM.finditer(t)]


def close(x, y, tol=0.02):
    m = max(abs(x), abs(y))
    return abs(x - y) <= m * tol if m > 0 else abs(x - y) < 1e-9


class Vals:
    def __init__(self, values):
        self.v = sorted({round(x, 6) for x in values})

    def has(self, x, tol=0.02):
        i = bisect.bisect_left(self.v, x * (1 - tol))
        j = bisect.bisect_right(self.v, x * (1 + tol))
        return any(close(x, y, tol) for y in self.v[i:j])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--workspaces", nargs="+", required=True)
    a = ap.parse_args(argv)
    exp = Path(a.export_dir)
    meta = [json.loads(l) for l in open(exp / "metadata_unified.jsonl")]
    split = load(exp / "split_manifest.json")
    wss = {Path(w).name: Path(w) for w in a.workspaces}
    # workspace results / sources
    state = {}
    for name, ws in wss.items():
        st = load(ws / "status.json")
        state[name] = {r["document_id"]: r for r in st["results"]}
    man = {}
    for name, ws in wss.items():
        for d in load(ws / "production_manifest.json"):
            d = dict(d)
            d["_ws"] = name
            man[d["document_id"]] = d
    res = {"records": len(meta), "format_errors": [], "artifact_docs": [],
           "unknown_citation_docs": [], "numeric_candidates": 0, "numeric_derived": 0,
           "numeric_format_variants": 0, "numeric_unexplained": [], "duplicates": [],
           "files": {}, "split": {"train": len(split["train"]), "val": len(split["val"]),
                                  "overlap": sorted(set(split["train"]) & set(split["val"])),
                                  "family_leak": []},
           "minor_ge1": sum(1 for m in meta if m["minor_count"] >= 1),
           "minors_total": sum(m["minor_count"] for m in meta),
           "repaired": sum(1 for m in meta if m.get("repaired"))}
    for fn in sorted(p.name for p in exp.glob("*.jsonl")):
        res["files"][fn] = {"lines": sum(1 for _ in open(exp / fn)), "bytes": (exp / fn).stat().st_size}
    seen = collections.Counter()
    fam = collections.defaultdict(set)
    vset = set(split["val"])
    for row in meta:
        d = row["document_id"]
        seen[d] += 1
        fam[row["split_family"]].add("val" if d in vset else "train")
        name = row.get("batch")
        ws = wss.get(name)
        if ws is None:
            res["format_errors"].append({"document_id": d, "error": "workspace missing"})
            continue
        try:
            q, v, bs, qa_sha, review_sha = resolve_effective(ws, state[name][d])
        except Exception as e:  # noqa: BLE001
            res["format_errors"].append({"document_id": d, "error": f"resolve: {e}"[:160]})
            continue
        tx = q.get("question", "") + "\n" + q.get("answer", "")
        for kind, rx in ARTIFACTS:
            if rx.search(tx):
                res["artifact_docs"].append({"document_id": d, "kind": kind})
                break
        blocks = {b["id"]: b["text"] for b in bs}
        refs = set(CITE.findall(tx))
        if not refs or not refs <= set(blocks):
            res["unknown_citation_docs"].append({"document_id": d})
        allv = Vals(nums(" ".join(blocks.values())))
        for sent in re.split(r"(?<=[.!?])\s+", q.get("answer", "")):
            cited = sorted(set(CITE.findall(sent)))
            if not cited:
                continue
            clean = CITE.sub(" ", sent)
            raws = [m.group(0) for m in NUM.finditer(clean)]
            if not raws:
                continue
            cited_text = " ".join(blocks[c] for c in cited if c in blocks)
            cn = nums(cited_text)
            cv = Vals(cn)
            cvs = sorted({round(x, 6) for x in cn})
            cited_norm = re.sub(r"[\s,]", "", cited_text.lower())
            all_norm = re.sub(r"[\s,]", "", " ".join(blocks.values()).lower())
            for raw in raws:
                x = float(raw.replace(",", ""))
                res["numeric_candidates"] += 1
                if cv.has(x) or allv.has(x):
                    res["numeric_derived"] += 1
                    continue
                ok = False
                for y in cvs:
                    if y == 0:
                        continue
                    for cand in (y / x if x else None, x / y if y else None, y + x, y - x,
                                 x - y, 100 * y / x if x else None):
                        if cand is not None and (cv.has(cand) or (cand <= 0 and cv.has(-cand))):
                            ok = True
                            break
                    if ok:
                        break
                if ok:
                    res["numeric_derived"] += 1
                    continue
                clean_raw = re.sub(r"[,\s]", "", raw)
                if clean_raw in cited_norm or clean_raw in all_norm:
                    res["numeric_format_variants"] += 1
                    continue
                parts = [p for p in re.split(r"[,/]", raw) if p]
                if len(parts) > 1 and all(p in cited_norm or p in all_norm for p in parts):
                    res["numeric_format_variants"] += 1
                    continue
                res["numeric_unexplained"].append({"document_id": d, "num": raw,
                                                   "sent": clean[:140]})
    res["duplicates"] = [d for d, c in seen.items() if c > 1][:10]
    res["split"]["family_leak"] = [f for f, s in fam.items() if len(s) > 1][:5]
    res["summary"] = {
        "format_error_count": len(res["format_errors"]),
        "artifact_docs": len({a["document_id"] for a in res["artifact_docs"]}),
        "unknown_citation_docs": len(res["unknown_citation_docs"]),
        "numeric_unexplained_count": len(res["numeric_unexplained"]),
        "numeric_unexplained_docs": len({u["document_id"] for u in res["numeric_unexplained"]}),
        "duplicates": len(res["duplicates"]),
        "split_clean": not res["split"]["overlap"] and not res["split"]["family_leak"],
    }
    (exp / "final_audit.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps({**res["summary"], "numeric_candidates": res["numeric_candidates"],
                      "numeric_derived": res["numeric_derived"],
                      "numeric_format_variants": res["numeric_format_variants"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
