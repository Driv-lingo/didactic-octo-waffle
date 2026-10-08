import time

from fastapi.testclient import TestClient

from minimum.persist import BlobSync
from minimum.store import Store
from minimum.web.app import create_app


class FakeBlob:
    def __init__(self):
        self.data = None
        self.uploads = 0

    def exists(self):
        return self.data is not None

    def download(self):
        return self.data

    def upload(self, data):
        self.data = data
        self.uploads += 1


def test_commit_schedules_debounced_upload(tmp_path):
    blob = FakeBlob()
    sync = BlobSync(blob, tmp_path / "l.db", debounce_s=0.05)
    assert sync.restore() is False
    store = Store(tmp_path / "l.db")
    sync.attach(store)
    store.create_learner("a", "science", "p1")
    store.log("a", "x", {})
    store.log("a", "y", {})
    time.sleep(0.3)
    assert blob.uploads == 1  # three commits, one upload
    assert blob.data and blob.data[:15] == b"SQLite format 3"


def test_restore_round_trip(tmp_path):
    blob = FakeBlob()
    sync = BlobSync(blob, tmp_path / "a.db", debounce_s=0.01)
    store = Store(tmp_path / "a.db")
    sync.attach(store)
    store.create_learner("tess", "science", "p2")
    sync.flush()
    store.close()
    # a fresh "container" with an empty disk
    sync2 = BlobSync(blob, tmp_path / "fresh" / "b.db")
    assert sync2.restore() is True
    store2 = Store(tmp_path / "fresh" / "b.db")
    assert store2.learner("tess")["current_phase"] == "p2"


def test_upload_failure_does_not_break_writes(tmp_path):
    class Broken(FakeBlob):
        def upload(self, data):
            raise RuntimeError("storage down")

    sync = BlobSync(Broken(), tmp_path / "c.db", debounce_s=0.01)
    store = Store(tmp_path / "c.db")
    sync.attach(store)
    store.create_learner("a", "science", "p1")
    sync.flush()
    assert "storage down" in sync.last_error
    assert store.learner("a") is not None


def test_web_app_reports_persistence_and_restores(tmp_path):
    blob = FakeBlob()
    app = create_app(db=tmp_path / "w.db", learner="web", offline=True, token="", blob_sync=BlobSync(blob, tmp_path / "w.db", debounce_s=0.01))
    with TestClient(app) as c:
        hz = c.get("/healthz").json()
        assert hz["persistence"]["mode"] == "blob" and hz["persistence"]["restored"] is False
        c.post("/enroll")
    # lifespan shutdown flushed
    assert blob.uploads >= 1
    app2 = create_app(db=tmp_path / "again" / "w.db", learner="web", offline=True, token="", blob_sync=BlobSync(blob, tmp_path / "again" / "w.db"))
    with TestClient(app2) as c:
        assert c.get("/healthz").json()["persistence"]["restored"] is True
        assert "Enroll" not in c.get("/").text  # already enrolled, restored from blob
