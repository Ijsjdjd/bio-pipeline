"""Export one or more QA workspaces into a family-safe, multi-format SFT dataset.

Usage:
  python export/export_sft.py --out /data/sft_export --workspaces WS1 [WS2 ...]
                              [--seed 20261105] [--val-fraction 0.08]

Each workspace must be a completed QA workspace (see pipeline/setup_workspace.py and
pipeline/runner.py): production_manifest.json, status.json, tasks/<doc>/papers/<doc>/...
Documents with an accepted repaired version under <workspace>/minor_repair_*/<doc>/qa.json
are resolved by matching the final qa hash recorded in status.json.

Outputs: metadata_unified.jsonl, split_manifest.json, stats.json, hashes.json,
llama_factory_example.yaml, swift_example.yaml and for each format x split:
{messages,sharegpt,swift,alpaca,text}_{train,val}.jsonl (+ *_ctx32k.jsonl).
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
from jinja2 import Environment  # noqa: E402

SYSTEM_PROMPT = ("Answer using only the supplied paper. Cite original evidence IDs, distinguish "
                 "reported results from interpretations and proposals, and acknowledge limitations.")
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


def sth(t):
    return hashlib.sha256(t.encode()).hexdigest()


def resolve_effective(ws: Path, result: dict):
    """Return (qa, review, sources, qa_sha, review_sha) for an accepted document."""
    d = result["document_id"]
    qa_sha = result["final_qa_sha256"]
    review_path = ws / result["final_review_path"]
    doc = {x["document_id"]: x for x in load(ws / "production_manifest.json")}[d]
    sources = load(ws / doc["sources"])
    candidates = []
    for folder in sorted(ws.glob("minor_repair_*")):
        qa = folder / d / "qa.json"
        if qa.exists():
            candidates.append(qa)
    for qa_path in candidates:
        if sha(qa_path) == qa_sha:
            rv = qa_path.parent / "review.json"
            if rv.exists():
                review_path = rv
            return load(qa_path), load(review_path), sources, qa_sha, sha(review_path)
    qa_path = ws / "tasks" / d / "papers" / d / "qa_formatted.json"
    return load(qa_path), load(review_path), sources, qa_sha, result["final_review_sha256"]


def build_rows(workspaces, tokenizer_path, template_path):
    tok = Tokenizer.from_file(str(tokenizer_path))
    template = Environment().from_string(Path(template_path).read_text())
    rows, excluded = [], []
    for ws in workspaces:
        ws = Path(ws)
        state = load(ws / "status.json")
        man = {d["document_id"]: d for d in load(ws / "production_manifest.json")}
        cls = {}
        if (ws / "selection.json").exists():
            for x in load(ws / "selection.json").get("documents", []):
                if x.get("classification"):
                    cls[x["document_id"]] = x["classification"]
        for r in state["results"]:
            if r.get("status") != "ready":
                continue
            d = r["document_id"]
            try:
                q, v, bs, qa_sha, review_sha = resolve_effective(ws, r)
            except Exception as e:  # noqa: BLE001
                excluded.append({"document_id": d, "error": str(e)[:200]})
                continue
            q_text = q.get("question", "") + "\n" + q.get("answer", "")
            hit = [name for name, rx in ARTIFACTS if rx.search(q_text)]
            if hit:
                excluded.append({"document_id": d, "artifacts": hit})
                continue
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "SOURCE MATERIAL\n" + pipeline.source(bs)
                                            + "\n\nQUESTION\n" + q["question"]},
                {"role": "assistant", "content": q["answer"]},
            ]
            text = template.render(messages=messages, add_generation_prompt=False,
                                   enable_thinking=False, preserve_thinking=False, tools=None)
            ids = tok.encode(text, add_special_tokens=False).ids
            decoded = tok.decode(ids, skip_special_tokens=False)
            norm = tok.normalizer.normalize_str(text) if tok.normalizer else text
            assert decoded == norm and tok.encode(decoded, add_special_tokens=False).ids == ids
            doc = man[d]
            row = {
                "document_id": d, "doi": doc["doi"], "version": str(doc["version"]),
                "publication_year": doc.get("publication_year"), "license": doc.get("license"),
                "split_family": doc["split_family"], "source_sha256": r["source_sha256"],
                "qa_sha256": qa_sha, "review_sha256": review_sha,
                "serialized_tokens": len(ids),
                "answer_tokens": len(tok.encode(q["answer"], add_special_tokens=False).ids),
                "text_sha256": sth(text), "minor_count": len(v.get("minor_issues") or []),
                "repaired": review_sha != r.get("final_review_sha256"), "batch": ws.name,
                "qa": q, "text": text, "messages": messages,
            }
            if d in cls:
                row["classification"] = cls[d]
            rows.append(row)
    return rows, excluded, tok


def write_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workspaces", nargs="+", required=True)
    ap.add_argument("--seed", default="20261105")
    ap.add_argument("--val-fraction", type=float, default=0.08)
    ap.add_argument("--tokenizer", default=str(REPO / "frozen/tokenizer/tokenizer.json"))
    ap.add_argument("--template", default=str(REPO / "frozen/tokenizer/chat_template.jinja"))
    a = ap.parse_args(argv)
    rows, excluded, tok = build_rows(a.workspaces, a.tokenizer, a.template)
    rows.sort(key=lambda r: r["document_id"])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    fam = collections.defaultdict(list)
    for i, r in enumerate(rows):
        fam[r["split_family"]].append(i)
    fams = sorted(fam, key=lambda f: hashlib.sha256(f"{a.seed}:{f}".encode()).hexdigest())
    target = round(len(rows) * a.val_fraction)
    val = set()
    for f in fams:
        if len(val) >= target:
            break
        val.update(fam[f])
    splits = {"train": [i for i in range(len(rows)) if i not in val], "val": sorted(val)}
    assert not ({rows[i]["split_family"] for i in splits["train"]}
                & {rows[i]["split_family"] for i in splits["val"]})

    for split, idxs in splits.items():
        lines = {k: [] for k in ["messages", "sharegpt", "swift", "alpaca", "text"]}
        for i in idxs:
            r = rows[i]
            msgs = r["messages"]
            sysm = next((x["content"] for x in msgs if x["role"] == "system"), "")
            usr = next((x["content"] for x in msgs if x["role"] == "user"), "")
            asst = next((x["content"] for x in msgs if x["role"] == "assistant"), "")
            lines["messages"].append({"document_id": r["document_id"], "doi": r["doi"],
                                      "version": r["version"], "messages": msgs})
            lines["sharegpt"].append({"document_id": r["document_id"], "doi": r["doi"],
                                      "version": r["version"], "system": sysm,
                                      "conversations": [{"from": "human", "value": usr},
                                                        {"from": "gpt", "value": asst}]})
            lines["swift"].append({"document_id": r["document_id"], "doi": r["doi"],
                                   "version": r["version"], "messages": msgs})
            lines["alpaca"].append({"document_id": r["document_id"], "doi": r["doi"],
                                    "version": r["version"], "instruction": usr, "input": "",
                                    "output": asst, "system": sysm})
            lines["text"].append({"document_id": r["document_id"], "doi": r["doi"],
                                  "version": r["version"], "text": r["text"]})
        for k, v in lines.items():
            write_jsonl(out / f"{k}_{split}.jsonl", v)
        ctx = [i for i in idxs if rows[i]["serialized_tokens"] <= 32768]
        pos = {i: j for j, i in enumerate(idxs)}
        for k, v in lines.items():
            write_jsonl(out / f"{k}_{split}_ctx32k.jsonl", [v[pos[i]] for i in ctx])

    meta = [{k: v for k, v in r.items() if k not in ("qa", "text", "messages")} for r in rows]
    write_jsonl(out / "metadata_unified.jsonl", meta)
    (out / "split_manifest.json").write_text(json.dumps(
        {"train": [rows[i]["document_id"] for i in splits["train"]],
         "val": [rows[i]["document_id"] for i in splits["val"]]}, indent=1))
    lens = sorted(r["serialized_tokens"] for r in rows)
    stats = {
        "records": len(rows), "train": len(splits["train"]), "val": len(splits["val"]),
        "tokens_total": sum(lens), "tokens_mean": round(sum(lens) / max(1, len(lens))),
        "repaired": sum(1 for r in rows if r["repaired"]),
        "minor_ge1": sum(1 for r in rows if r["minor_count"] >= 1),
        "batch_counts": dict(collections.Counter(r["batch"] for r in rows)),
        "length_buckets": dict(collections.Counter(
            "<=8k" if x <= 8192 else "8-16k" if x <= 16384 else "16-24k" if x <= 24576
            else "24-32k" if x <= 32768 else ">32k" for x in lens)),
        "ctx32k": {"train": sum(1 for i in splits["train"] if rows[i]["serialized_tokens"] <= 32768),
                   "val": sum(1 for i in splits["val"] if rows[i]["serialized_tokens"] <= 32768),
                   "dropped": sum(1 for x in lens if x > 32768)},
        "excluded": excluded,
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    for name in ("llama_factory_example.yaml", "swift_example.yaml"):
        src = REPO / "configs" / name
        if src.exists():
            (out / name).write_text(src.read_text())
    hashes = {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "hashes.json"}
    (out / "hashes.json").write_text(json.dumps(hashes, indent=1) + "\n")
    print(json.dumps({k: stats[k] for k in ("records", "train", "val", "tokens_total",
                                            "repaired", "ctx32k")}, ensure_ascii=False))
    if excluded:
        print(f"excluded {len(excluded)} documents (first: {excluded[:3]})", file=sys.stderr)


if __name__ == "__main__":
    main()
