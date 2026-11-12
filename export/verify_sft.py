"""Full-row verification of an exported SFT dataset against its QA workspaces.

Checks per row: messages/text match the accepted QA and source blocks, sha256 hashes,
tokenizer token counts, internal artifact patterns, citation IDs present in source blocks.
Checks split: train/val overlap and family leakage. Writes verification_final.json.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "frozen" / "pipeline"), str(REPO / "pipeline"), str(REPO)]

import run as pipeline  # noqa: E402
from tokenizers import Tokenizer  # noqa: E402

SYSTEM_PROMPT = ("Answer using only the supplied paper. Cite original evidence IDs, distinguish "
                 "reported results from interpretations and proposals, and acknowledge limitations.")
CITE = re.compile(r"biorxiv_[A-Za-z0-9_]+:b\d+")
ARTIFACTS = [
    ("document_placeholder", re.compile(r"\bdocument:", re.I)),
    ("dag_meta", re.compile(r"unverified DAG|DAG's|DAG correctly|DAG notes|DAG flags|DAG records|"
                            r"DAG labels|DAG limitation|stated in the DAG|in the DAG\b|the DAG\b|"
                            r"DAG assumption|DAG node|DAG treats|DAG itself|DAG statement", re.I)),
    ("node_artifact", re.compile(r"\bn0\d{2}\b", re.I)),
    ("generation_artifact", re.compile(r"generation artifact|system prompt", re.I)),
    ("ai_apology", re.compile(r"as an AI|I\u2019m sorry|I am sorry", re.I)),
]


def load(p):
    return json.loads(Path(p).read_text())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def resolve(ws, meta_row):
    """Return (qa, sources) for a document, matching the exported qa hash."""
    d = meta_row["document_id"]
    target = meta_row["qa_sha256"]
    for folder in sorted(Path(ws).glob("minor_repair_*")):
        qa = folder / d / "qa.json"
        if qa.exists() and sha(qa) == target:
            doc = {x["document_id"]: x for x in load(Path(ws) / "production_manifest.json")}[d]
            return load(qa), load(Path(ws) / doc["sources"])
    doc = {x["document_id"]: x for x in load(Path(ws) / "production_manifest.json")}[d]
    qa = Path(ws) / "tasks" / d / "papers" / d / "qa_formatted.json"
    return load(qa), load(Path(ws) / doc["sources"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--workspaces", nargs="+", required=True)
    ap.add_argument("--tokenizer", default=str(REPO / "frozen/tokenizer/tokenizer.json"))
    a = ap.parse_args(argv)
    exp = Path(a.export_dir)
    meta = [json.loads(l) for l in open(exp / "metadata_unified.jsonl")]
    split_manifest = load(exp / "split_manifest.json")
    msgs = {s: [json.loads(l) for l in open(exp / f"messages_{s}.jsonl")] for s in ("train", "val")}
    texts = {s: [json.loads(l) for l in open(exp / f"text_{s}.jsonl")] for s in ("train", "val")}
    tok = Tokenizer.from_file(a.tokenizer)
    wss = {Path(w).name: Path(w) for w in a.workspaces}
    errors, hash_bad, tok_bad, artifacts = [], [], [], []
    checked = 0
    for split in ("train", "val"):
        for j, d in enumerate(split_manifest[split]):
            row = next((m for m in meta if m["document_id"] == d), None)
            if row is None:
                errors.append({"document_id": d, "error": "metadata row missing"})
                continue
            ws = wss.get(row.get("batch"))
            if ws is None:
                errors.append({"document_id": d, "error": f"workspace not supplied: {row.get('batch')}"})
                continue
            try:
                q, bs = resolve(ws, row)
            except Exception as e:  # noqa: BLE001
                errors.append({"document_id": d, "error": f"resolve: {e}"[:200]})
                continue
            expected = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "SOURCE MATERIAL\n" + pipeline.source(bs)
                                            + "\n\nQUESTION\n" + q["question"]},
                {"role": "assistant", "content": q["answer"]},
            ]
            actual = [{k: v for k, v in m.items() if k not in ("document_id", "doi", "version")}
                      for m in msgs[split][j].get("messages", [])]
            if actual != expected:
                # tolerate id fields in older exports
                if [{k: x[k] for k in ("role", "content")} for x in msgs[split][j]["messages"]] != expected:
                    hash_bad.append({"document_id": d, "kind": "messages_mismatch"})
            text = texts[split][j]["text"]
            if hashlib.sha256(text.encode()).hexdigest() != row["text_sha256"]:
                hash_bad.append({"document_id": d, "kind": "text_sha_mismatch"})
            n = len(tok.encode(text, add_special_tokens=False).ids)
            if abs(n - row["serialized_tokens"]) > 1:
                tok_bad.append({"document_id": d, "expected": row["serialized_tokens"], "actual": n})
            tx = q.get("question", "") + "\n" + q.get("answer", "")
            for name, rx in ARTIFACTS:
                if rx.search(tx):
                    artifacts.append({"document_id": d, "kind": name})
                    break
            known = {b["id"] for b in bs}
            if not set(CITE.findall(tx)) <= known:
                errors.append({"document_id": d, "error": "citation id not in source blocks"})
            checked += 1
    fam = collections.defaultdict(set)
    for row in meta:
        fam[row["split_family"]].add("val" if row["document_id"] in set(split_manifest["val"]) else "train")
    leak = [f for f, s in fam.items() if len(s) > 1]
    res = {"records": len(meta), "checked": checked, "error_count": len(errors),
           "errors": errors[:5], "hash_mismatch": len(hash_bad), "hash_sample": hash_bad[:5],
           "token_mismatch": len(tok_bad), "token_sample": tok_bad[:5],
           "artifact_hits": len(artifacts), "artifact_sample": artifacts[:5],
           "split": {"train": len(split_manifest["train"]), "val": len(split_manifest["val"]),
                     "overlap": sorted(set(split_manifest["train"]) & set(split_manifest["val"])),
                     "family_leak": leak[:5]},
           "minor_ge1": sum(1 for m in meta if m["minor_count"] >= 1),
           "total_minors": sum(m["minor_count"] for m in meta),
           "repaired": sum(1 for m in meta if m.get("repaired"))}
    (exp / "verification_final.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in res.items()
                      if k not in ("errors", "hash_sample", "token_sample", "artifact_sample")},
                     ensure_ascii=False))
    raise SystemExit(0 if res["error_count"] == 0 and res["hash_mismatch"] == 0
                     and res["token_mismatch"] == 0 and res["artifact_hits"] == 0 else 1)


if __name__ == "__main__":
    main()
