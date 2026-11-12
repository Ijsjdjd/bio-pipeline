"""Build a QA workspace from a directory of prepared evidence documents.

Workspace layout (matches pipeline/runner.py expectations):
    WORKSPACE/
      config.json                 # copied from configs/config.example.json (budget adjusted)
      frozen -> <repo>/frozen     # frozen prompts / schema / node & edge types / tokenizer
      inputs/<document_id>.json   # source blocks: [{id, section, text, ...}]
      production_manifest.json    # document records used by the runner
      production_input_hashes.json
      protected_external_hashes.json
      import_hashes.json
      eval_metadata.jsonl         # optional; documents in it are rejected as eval overlap
      budget.sqlite               # created on first run by budget_guard
      tasks/                      # per-document pipeline output (created by runner)

Evidence layout expected per document:
    EVIDENCE_DIR/<document_id>/{article.xml, blocks.json, source_validation.json}

Selection JSON (same format as produced upstream):
    {"documents": [{"document_id": ..., "doi": ..., "version": ..., "title": ...,
                    "classification": {...}, "source_dir": optional,
                    "xml_sha256": optional}]}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "frozen" / "pipeline"), str(REPO / "pipeline"), str(REPO)]

from lxml import etree as X  # noqa: E402

import run as pipe  # noqa: E402  (frozen pipeline lib: graph_check/source/etc.)
from formatting import identifier_set, validate  # noqa: E402,F401


def load(p):
    return json.loads(Path(p).read_text())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, x):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2) + "\n")


def build_workspace(evidence_dir, selection_path, out_dir, config_path, budget_cny=None,
                    eval_metadata=None):
    evidence_dir, selection_path = Path(evidence_dir), Path(selection_path)
    out_dir, config_path = Path(out_dir), Path(config_path)
    assert not (out_dir / "production_manifest.json").exists(), "workspace already built"
    sel = load(selection_path)
    docs_in = sel["documents"]
    cfg = load(config_path)
    if budget_cny is not None:
        cfg["budget_cny"] = float(budget_cny)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "inputs").mkdir(exist_ok=True)
    frozen_link = out_dir / "frozen"
    if not frozen_link.exists():
        frozen_link.symlink_to((REPO / "frozen").resolve(), target_is_directory=True)
    save(out_dir / "config.json", cfg)

    eval_hashes = None
    if eval_metadata:
        shutil.copy2(eval_metadata, out_dir / "eval_metadata.jsonl")
        eval_hashes = sha(out_dir / "eval_metadata.jsonl")

    manifest, protected = [], {}
    for rec in docs_in:
        i = rec["document_id"]
        src = Path(rec.get("source_dir") or (evidence_dir / i))
        assert (src / "article.xml").exists(), f"missing article.xml for {i}"
        if rec.get("xml_sha256"):
            assert sha(src / "article.xml") == rec["xml_sha256"], f"xml hash mismatch {i}"
        protected[str(src / "article.xml")] = sha(src / "article.xml")
        protected[str(src / "source_validation.json")] = sha(src / "source_validation.json")

        blocks = load(src / "blocks.json")
        save(out_dir / "inputs" / f"{i}.json", blocks)

        root = X.fromstring((src / "article.xml").read_bytes(),
                            X.XMLParser(resolve_entities=False, no_network=True))
        meta = root.find("front/article-meta")
        years = [int("".join(e.itertext()).strip()) for e in meta.findall("pub-date/year")
                 if "".join(e.itertext()).strip().isdigit()]
        assert years and min(years) >= 2020, f"missing/invalid publication year for {i}"
        tree = root.getroottree()
        for b in blocks:  # verify block text against the XML xpath
            if "xml_xpath" in b:
                text = re.sub(r"\s+", " ", "".join(tree.xpath(b["xml_xpath"])[0].itertext())).strip()
                assert text[b["char_start"]:b["char_end"]] == b["text"], f"block mismatch {i}"

        doc = {
            "document_id": i,
            "sources": f"inputs/{i}.json",
            "title": rec.get("title", ""),
            "doi": rec["doi"],
            "venue": "bioRxiv preprint",
            "split_family": rec["doi"].lower(),
            "focus": cfg.get("focus") or "evidence-grounded long-form reasoning",
            "source_text_sha256": sha(out_dir / "inputs" / f"{i}.json"),
            "publication_year": min(years),
            "publication_date": None,
            "date_provenance": "JATS pub-date/year; exact posting day not verified",
            "version": str(rec.get("version", "1")),
            "license": [e.get("{http://www.w3.org/1999/xlink}href", "")
                        for e in meta.findall("permissions/license")],
            "category": [],
            "source_scope": ("All cached JATS reconstructed body paragraphs/captions/tables/equations; "
                             "no figure pixels or external supplements"),
            "additional_source_ids": [],
        }
        if rec.get("classification"):
            doc["classification"] = rec["classification"]
        manifest.append(doc)

    for f in (out_dir / "frozen" / "pipeline").glob("*.py"):
        protected[str(f.resolve())] = sha(f)
    for n in ["schema.json", "node_types.json", "edge_types.json",
              "tokenizer/tokenizer.json", "tokenizer/chat_template.jinja"]:
        protected[str((out_dir / "frozen" / n).resolve())] = sha(out_dir / "frozen" / n)

    save(out_dir / "production_manifest.json", manifest)
    save(out_dir / "production_input_hashes.json",
         {str(p.relative_to(out_dir)): sha(p)
          for p in [out_dir / "production_manifest.json"] + list((out_dir / "inputs").glob("*.json"))})
    import_hashes = {"selection.json": sha(selection_path)}
    if eval_hashes:
        import_hashes["eval_metadata.jsonl"] = eval_hashes
    save(out_dir / "import_hashes.json", import_hashes)
    save(out_dir / "protected_external_hashes.json", protected)
    return {"prepared": len(manifest), "workspace": str(out_dir),
            "protected_files": len(protected)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evidence-dir", required=True)
    ap.add_argument("--selection", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=str(REPO / "configs" / "config.example.json"))
    ap.add_argument("--budget-cny", type=float)
    ap.add_argument("--eval-metadata")
    a = ap.parse_args(argv)
    print(json.dumps(build_workspace(a.evidence_dir, a.selection, a.out, a.config,
                                     a.budget_cny, a.eval_metadata), ensure_ascii=False))


if __name__ == "__main__":
    main()
