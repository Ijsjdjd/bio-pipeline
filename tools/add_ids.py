"""Add bioRxiv identifiers (document_id/doi/version) to every row of an exported SFT dataset.

Usage: python tools/add_ids.py --export-dir DIR
Adds the three fields to all format files (messages/sharegpt/swift/alpaca/text x
train/val x full/ctx32k), using metadata_unified.jsonl order and the ctx32k token rule.
"""
import argparse
import json
import sys
from pathlib import Path

EXPECTED = ["messages", "sharegpt", "swift", "alpaca", "text"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export-dir", required=True)
    a = ap.parse_args(argv)
    d = Path(a.export_dir)
    meta = [json.loads(l) for l in open(d / "metadata_unified.jsonl")]
    info = {m["document_id"]: m for m in meta}
    split = json.loads((d / "split_manifest.json").read_text())
    for sp in ("train", "val"):
        ids = split[sp]
        ctx_ids = [m["document_id"] for m in meta
                   if m["document_id"] in set(ids) and m["serialized_tokens"] <= 32768]
        for fmt in EXPECTED:
            for suffix, order in (("", ids), ("_ctx32k", ctx_ids)):
                p = d / f"{fmt}_{sp}{suffix}.jsonl"
                if not p.exists():
                    continue
                out = []
                for line, doc in zip(open(p), order):
                    row = json.loads(line)
                    m = info[doc]
                    row["document_id"] = doc
                    row["doi"] = m["doi"]
                    row["version"] = str(m["version"])
                    out.append(row)
                p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
                print("updated", p.name, len(out))
    print("done")


if __name__ == "__main__":
    main()
