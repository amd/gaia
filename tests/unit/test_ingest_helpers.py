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


class _Session:
    def __init__(self, rag_enabled):
        self.rag_enabled = rag_enabled
        self.calls = []

    def enable_rag(self, documents):
        self.calls.append(("enable_rag", documents))
        self.rag_enabled = True
        return True

    def add_document(self, path):
        self.calls.append(("add_document", path))
        return True


def test_first_document_enables_rag_on_the_session():
    session = _Session(rag_enabled=False)
    assert ingest_document_to_rag("doc.txt", session) is True
    assert session.calls == [("enable_rag", ["doc.txt"])]


def test_later_documents_are_added_to_the_same_index():
    session = _Session(rag_enabled=True)
    assert ingest_document_to_rag("doc2.txt", session) is True
    assert session.calls == [("add_document", "doc2.txt")]
