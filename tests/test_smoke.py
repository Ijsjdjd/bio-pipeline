"""Offline smoke test: frozen assets + workspace build + export + verify + audit.

Run:  python -m pytest tests/ -q      (or: python tests/test_smoke.py)
Requires: jsonschema, tokenizers, jinja2, lxml.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "fixtures" / "synthetic"


def sha(p):
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_frozen_manifest():
    manifest = {}
    for line in (REPO / "frozen/MANIFEST.sha256").read_text().splitlines():
        h, name = line.split("  ", 1)
        manifest[name] = h
    assert manifest, "empty frozen manifest"
    for name, expected in manifest.items():
        assert sha(REPO / name) == expected, f"frozen asset changed: {name}"
    for required in ["schema.json", "node_types.json", "edge_types.json",
                     "tokenizer/tokenizer.json", "tokenizer/chat_template.jinja",
                     "pipeline/prompts.json", "pipeline/run.py"]:
        assert (REPO / "frozen" / required).exists(), required
    json.loads((REPO / "frozen/pipeline/prompts.json").read_text())


def test_build_workspace_export_verify_audit():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        ws = td / "workspace"
        # 1) build workspace from synthetic evidence
        subprocess.run([sys.executable, str(REPO / "pipeline/setup_workspace.py"),
                        "--evidence-dir", str(FIX / "evidence"),
                        "--selection", str(FIX / "selection.json"),
                        "--out", str(ws),
                        "--config", str(REPO / "configs/config.example.json"),
                        "--budget-cny", "1"], check=True)
        man = json.loads((ws / "production_manifest.json").read_text())
        assert len(man) == 1 and (ws / "inputs/biorxiv_10_9999_2026_01_01_000001_v1.json").exists()
        assert (ws / "frozen").exists()
        # 2) install synthetic accepted artifacts as if produced by the pipeline
        paper = ws / "tasks/biorxiv_10_9999_2026_01_01_000001_v1/papers/biorxiv_10_9999_2026_01_01_000001_v1"
        paper.mkdir(parents=True)
        for name in ("qa_formatted.json", "argon_final.json"):
            (paper / name).write_text((FIX / "accepted" / name).read_text())
        (paper / "qa.json").write_text((FIX / "accepted/qa_formatted.json").read_text())
        (paper / "dag.json").write_text(json.dumps({"document_id": "biorxiv_10_9999_2026_01_01_000001_v1", "nodes": [], "edges": []}))
        status = {"status": "finished", "results": [{
            "document_id": "biorxiv_10_9999_2026_01_01_000001_v1", "status": "ready",
            "source_sha256": man[0]["source_text_sha256"],
            "final_qa_sha256": sha(paper / "qa_formatted.json"),
            "final_review_sha256": sha(paper / "argon_final.json"),
            "final_review_path": "tasks/biorxiv_10_9999_2026_01_01_000001_v1/papers/biorxiv_10_9999_2026_01_01_000001_v1/argon_final.json",
            "quality": "pass",
        }]}
        (ws / "status.json").write_text(json.dumps(status))
        # 3) export
        out = td / "sft"
        subprocess.run([sys.executable, str(REPO / "export/export_sft.py"),
                        "--out", str(out), "--workspaces", str(ws)], check=True)
        stats = json.loads((out / "stats.json").read_text())
        assert stats["records"] == 1, stats
        meta = [json.loads(l) for l in open(out / "metadata_unified.jsonl")]
        assert meta[0]["document_id"] == "biorxiv_10_9999_2026_01_01_000001_v1"
        row = json.loads(open(out / "messages_train.jsonl").readline())
        assert row["document_id"] == "biorxiv_10_9999_2026_01_01_000001_v1" and row["doi"] == "10.9999/demo.001"
        # 4) verify
        r = subprocess.run([sys.executable, str(REPO / "export/verify_sft.py"),
                            "--export-dir", str(out), "--workspaces", str(ws)])
        assert r.returncode == 0, "verify_sft failed"
        v = json.loads((out / "verification_final.json").read_text())
        assert v["hash_mismatch"] == 0 and v["token_mismatch"] == 0 and v["artifact_hits"] == 0
        # 5) audit
        subprocess.run([sys.executable, str(REPO / "export/audit_numeric.py"),
                        "--export-dir", str(out), "--workspaces", str(ws)], check=True)
        audit = json.loads((out / "final_audit.json").read_text())
        assert audit["summary"]["artifact_docs"] == 0
        assert audit["summary"]["unknown_citation_docs"] == 0


if __name__ == "__main__":
    test_frozen_manifest()
    test_build_workspace_export_verify_audit()
    print("smoke tests passed")
