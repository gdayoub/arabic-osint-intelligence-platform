"""Tests for src/store/blob.py — the BlobStore abstraction (M1.5)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.config.settings import Settings
from src.store.blob import (
    LocalDiskBlobStore,
    R2BlobStore,
    compress_text,
    decompress_text,
    get_blob_store,
    sha256_text,
    text_blob_key,
)


def test_put_get_roundtrip(blob_store):
    blob_store.put("some/key.txt", b"hello world")
    assert blob_store.get("some/key.txt") == b"hello world"


def test_exists_false_then_true(blob_store):
    assert blob_store.exists("missing/key.txt") is False
    blob_store.put("missing/key.txt", b"now it's here")
    assert blob_store.exists("missing/key.txt") is True


def test_get_missing_key_raises_key_error(blob_store):
    with pytest.raises(KeyError):
        blob_store.get("never/written.txt")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello world",
        "أعلن الرئيس عن سياسة جديدة",  # plain Arabic
        "مُظَاهَرَة",  # Arabic with diacritics (tashkeel)
        "طـــويل",  # Arabic with tatweel
        "café 🇸🇦 emoji test",  # multi-byte emoji
        "é",  # "e" + combining acute accent
    ],
)
def test_compress_decompress_roundtrip(text):
    assert decompress_text(compress_text(text)) == text


def test_compression_is_deterministic():
    text = "أعلن الرئيس عن سياسة جديدة تجاه الشرق الأوسط."
    assert compress_text(text) == compress_text(text)


def test_text_blob_key_shape():
    h = sha256_text("some article text")
    key = text_blob_key(h)
    assert key == f"v1/documents/{h[:2]}/{h}.txt.gz"


def test_get_blob_store_defaults_to_local(tmp_path):
    settings = Settings(blob_backend="local", blob_local_root=str(tmp_path))
    store = get_blob_store(settings)
    assert isinstance(store, LocalDiskBlobStore)


def test_get_blob_store_r2_with_all_creds_set():
    # Constructing a boto3 client does no network I/O, so this is safe offline.
    settings = Settings(
        blob_backend="r2",
        r2_endpoint_url="https://example.r2.cloudflarestorage.com",
        r2_bucket="test-bucket",
        r2_access_key_id="fake-id",
        r2_secret_access_key="fake-secret",
    )
    store = get_blob_store(settings)
    assert isinstance(store, R2BlobStore)


def test_r2_put_forwards_the_callers_content_type():
    store = object.__new__(R2BlobStore)
    store._bucket = "test-bucket"
    store._client = Mock()

    store.put(
        "v1/releases/example.json",
        b"{}\n",
        content_type="application/json; charset=utf-8",
    )

    store._client.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="v1/releases/example.json",
        Body=b"{}\n",
        ContentType="application/json; charset=utf-8",
    )


def test_get_blob_store_r2_missing_creds_raises_with_var_names():
    settings = Settings(blob_backend="r2", r2_bucket="test-bucket")  # missing 3 of 4
    with pytest.raises(RuntimeError, match="R2_ENDPOINT_URL"):
        get_blob_store(settings)


def test_get_blob_store_unknown_backend_raises():
    settings = Settings(blob_backend="s3")
    with pytest.raises(ValueError, match="Unknown BLOB_BACKEND"):
        get_blob_store(settings)
