"""Keep the SQLite learner database safe in Azure Blob Storage.

The database file stays local, so every read and write is a plain SQLite
operation. On startup the latest copy is restored from the blob if one
exists. After every commit an upload is scheduled; uploads are debounced
so a burst of writes becomes one upload, and a final upload runs at
shutdown. The copy uploaded is a consistent snapshot taken with the
SQLite backup API, never the live file mid-write.

This is deliberately simpler than a managed database: one learner, one
replica, a few kilobytes per save. It is not safe for several replicas
writing at once.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Protocol

log = logging.getLogger("minimum.persist")


class BlobLike(Protocol):
    """The three calls we use. The Azure client and the test fake both provide them."""

    def exists(self) -> bool: ...
    def download(self) -> bytes: ...
    def upload(self, data: bytes) -> None: ...


class AzureBlob:
    def __init__(self, connection_string: str, container: str, name: str):
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient.from_connection_string(connection_string)
        try:
            service.create_container(container)
        except Exception:  # noqa: BLE001 - already exists, or no permission to create (then upload will say so)
            pass
        self.client = service.get_blob_client(container=container, blob=name)

    def exists(self) -> bool:
        return bool(self.client.exists())

    def download(self) -> bytes:
        return self.client.download_blob().readall()

    def upload(self, data: bytes) -> None:
        self.client.upload_blob(data, overwrite=True)


class BlobSync:
    def __init__(self, blob: BlobLike, db_path: str | Path, debounce_s: float = 2.0):
        self.blob = blob
        self.db_path = Path(db_path)
        self.debounce_s = debounce_s
        self._store = None
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self.uploads = 0
        self.last_error: str | None = None

    # --- startup -----------------------------------------------------------
    def restore(self) -> bool:
        """Download the saved database to db_path if a saved copy exists. Returns True if restored."""
        if not self.blob.exists():
            return False
        data = self.blob.download()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.db_path.with_suffix(".restore")
        tmp.write_bytes(data)
        os.replace(tmp, self.db_path)
        log.info("restored learner database from blob (%d bytes)", len(data))
        return True

    def attach(self, store) -> None:
        """Hook the store so every commit schedules an upload."""
        self._store = store
        store.on_commit = self.schedule

    # --- uploads -----------------------------------------------------------
    def schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_s, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        """Upload a consistent snapshot now."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._store is None:
            return
        try:
            with tempfile.TemporaryDirectory() as td:
                snap = Path(td) / "snapshot.db"
                self._store.snapshot(snap)
                data = snap.read_bytes()
            self.blob.upload(data)
            self.uploads += 1
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - never let a save failure crash a request
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.error("learner database upload failed: %s", self.last_error)


def blob_sync_from_env(db_path: str | Path) -> BlobSync | None:
    conn = os.environ.get("MINIMUM_BLOB_CONNECTION", "")
    if not conn:
        return None
    container = os.environ.get("MINIMUM_BLOB_CONTAINER", "learnerdata")
    name = os.environ.get("MINIMUM_BLOB_NAME", "learner.db")
    return BlobSync(AzureBlob(conn, container, name), db_path)
