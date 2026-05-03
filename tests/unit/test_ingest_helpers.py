import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging.ingest import ingest_document_to_rag, ingest_image_to_vlm


class DummyVLM:
    def __init__(self, *args, **kwargs):
        self.vlm_model = "dummy-vlm"

    def extract_from_image(self, image_bytes, **kwargs):
        return "extracted-text"


def test_ingest_image_to_vlm_monkeypatch(monkeypatch):
    # Patch VLMClient used by ingest helpers
    monkeypatch.setattr("gaia.messaging.ingest.VLMClient", DummyVLM)

    with tempfile.NamedTemporaryFile(delete=False) as tf:
        tf.write(b"\x89PNG\r\n\x1a\n" + b"data")
        tf.flush()
        path = tf.name

    try:
        res = ingest_image_to_vlm(path)
        assert res["status"] == "success"
        assert "extracted-text" in res["text"]
    finally:
        os.remove(path)


def test_ingest_document_to_rag_monkeypatch(monkeypatch, tmp_path):
    # Patch RAGSDK to avoid heavy dependencies
    class DummyRAG:
        def __init__(self, cfg):
            pass

        def index_document(self, file_path):
            return {"success": True, "file_name": os.path.basename(file_path)}

    monkeypatch.setattr("gaia.messaging.ingest.RAGSDK", DummyRAG)

    p = tmp_path / "doc.txt"
    p.write_text("hello world")
    res = ingest_document_to_rag(str(p))
    assert res.get("success") is True
