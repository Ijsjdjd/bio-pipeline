"""Classification distribution of an exported SFT dataset (domain / subdomain / topic).

Usage: python tools/distribution.py --export-dir DIR --workspaces WS [WS ...]
Writes distribution.json and distribution.md into the export directory.
"""
import argparse
import collections
import json
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--workspaces", nargs="+", required=True)
    a = ap.parse_args(argv)
    d = Path(a.export_dir)
    meta = [json.loads(l) for l in open(d / "metadata_unified.jsonl")]
    cls = {}
    for ws in a.workspaces:
        ws = Path(ws)
        if (ws / "selection.json").exists():
            for doc in json.loads((ws / "selection.json").read_text()).get("documents", []):
                if doc.get("classification"):
                    cls[doc["document_id"]] = doc["classification"]
    rows = []
    for m in meta:
        c = cls.get(m["document_id"])
        if c:
            rows.append(c)
    def counts(key):
        return collections.Counter(r[key] for r in rows)
    def names(key, name_key):
        return {r[key]: r[name_key] for r in rows}
    dom, sub, top = counts("domain_id"), counts("subdomain_id"), counts("topic_id")
    dn, sn, tn = names("domain_id", "domain"), names("subdomain_id", "subdomain"), names("topic_id", "topic")
    total = len(rows)
    res = {"records": len(meta), "classified": total,
           "domain": {k: {"name": dn[k], "count": v, "pct": round(v / total * 100, 2)}
                      for k, v in dom.most_common()},
           "subdomain": {k: {"name": sn[k], "count": v, "pct": round(v / total * 100, 2)}
                         for k, v in sub.most_common()},
           "topics_covered": len(top)}
    (d / "distribution.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    lines = ["# Classification distribution", "",
             f"records: {len(meta)} (classified: {total}); topics covered: {len(top)}", "",
             "## Domain", "", "| Domain | Name | Count | % |", "|---|---|---:|---:|"]
    for k, v in dom.most_common():
        lines.append(f"| {k} | {dn[k]} | {v} | {round(v / total * 100, 2)} |")
    lines += ["", "## Subdomain", "", "| Subdomain | Name | Count | % |", "|---|---|---:|---:|"]
    for k, v in sub.most_common():
        lines.append(f"| {k} | {sn[k]} | {v} | {round(v / total * 100, 2)} |")
    (d / "distribution.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({k: v["count"] for k, v in res["domain"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
