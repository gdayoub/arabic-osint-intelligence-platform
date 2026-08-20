"""Content-addressed blob storage for document text (M1.5).

Document text is large, rarely queried relationally, and compresses well —
none of which is true of the metadata sitting next to it in Postgres. It
lives here instead, addressed by a hash of its own content, with Postgres
holding only a pointer. See docs/adr/0004-document-text-in-blob-storage.md.

Everything that stores or fetches document text goes through a BlobStore.
Nothing outside this module imports boto3 or knows R2 exists — swapping
backends (or adding a third) means one more class and one more branch in
get_blob_store(), nothing else in the codebase changes.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
from pathlib import Path
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from src.config.settings import SETTINGS, Settings

KEY_PREFIX = "v1/documents"


class BlobStore(Protocol):
    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None: ...

    def get(self, key: str) -> bytes:
        """Raise KeyError if `key` doesn't exist — never a backend-specific exception."""
        ...

    def exists(self, key: str) -> bool: ...


class LocalDiskBlobStore:
    """Default backend: plain files under a root directory.

    No credentials, no network — this is what runs when a fresh clone hasn't
    configured R2 yet, and what the test suite uses so tests need neither.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        return self._root / key

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        # Local files do not carry HTTP metadata.  Keeping the keyword in the
        # interface lets the exact same caller send correct metadata to R2.
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file, then rename. os.replace() is atomic on
        # both POSIX and Windows, so a crash mid-write never leaves a
        # truncated blob sitting at the real key.
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_bytes(data)
        os.replace(tmp_path, path)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise KeyError(key) from None

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class R2BlobStore:
    """Cloudflare R2, via boto3's S3-compatible API.

    R2-specific quirks handled here so callers don't have to know them:
    region_name must be "auto"; head_object reports a missing key as HTTP
    404, not the "NoSuchKey" code get_object uses, so both are checked.
    """

    def __init__(self, *, endpoint_url: str, bucket: str, access_key_id: str, secret_access_key: str) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
        )

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            if _is_not_found(exc):
                raise KeyError(key) from None
            raise

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise


def _is_not_found(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code")
    return code in ("NoSuchKey", "404")


def compress_text(text: str) -> bytes:
    """gzip-compress UTF-8 text, deterministically.

    Plain gzip.compress() embeds the current time in its header, so
    compressing the same string twice produces different bytes — which
    breaks "did I already write this" reasoning and makes tests flaky.
    Fixing mtime=0 makes the output a pure function of the input.
    """
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=6, mtime=0) as gz_file:
        gz_file.write(text.encode("utf-8"))
    return buffer.getvalue()


def decompress_text(data: bytes) -> str:
    return gzip.decompress(data).decode("utf-8")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text_blob_key(text_sha256: str) -> str:
    """Content-addressed key, e.g. v1/documents/3f/3f9a...c1.txt.gz

    Content-addressing (keying on a hash of the text, not the document id)
    means: the same key is produced no matter who writes it, so a write can
    always check `exists()` first and skip re-uploading; two documents with
    identical text share one blob for free; and the key is known before the
    database row exists, so there's no ordering dependency between the two.

    The 2-character fan-out directory isn't for R2/S3 performance — that's
    an outdated rule from early S3. It's here so LocalDiskBlobStore never
    dumps 100k files in one directory, where `ls` and some filesystems slow
    down.
    """
    return f"{KEY_PREFIX}/{text_sha256[:2]}/{text_sha256}.txt.gz"


def get_blob_store(settings: Settings | None = None) -> BlobStore:
    """Select a backend from config. This is the swap point: changing
    BLOB_BACKEND (and the four R2_* vars) moves document text from local
    disk to Cloudflare R2 with no other code change.
    """
    settings = settings or SETTINGS

    if settings.blob_backend == "local":
        return LocalDiskBlobStore(root=settings.blob_local_root)

    if settings.blob_backend == "r2":
        required = {
            "R2_ENDPOINT_URL": settings.r2_endpoint_url,
            "R2_BUCKET": settings.r2_bucket,
            "R2_ACCESS_KEY_ID": settings.r2_access_key_id,
            "R2_SECRET_ACCESS_KEY": settings.r2_secret_access_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(
                f"BLOB_BACKEND=r2 but missing env var(s): {', '.join(missing)}"
            )
        return R2BlobStore(
            endpoint_url=settings.r2_endpoint_url,  # type: ignore[arg-type]
            bucket=settings.r2_bucket,  # type: ignore[arg-type]
            access_key_id=settings.r2_access_key_id,  # type: ignore[arg-type]
            secret_access_key=settings.r2_secret_access_key,  # type: ignore[arg-type]
        )

    raise ValueError(f"Unknown BLOB_BACKEND {settings.blob_backend!r}; expected 'local' or 'r2'")
