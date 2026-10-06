"""S3 boundary tests use injected clients, never claim live cloud validation."""

import hashlib
import io

import pytest
from botocore.exceptions import ClientError

from preact.engines.storage import S3Artifacts


def test_s3_missing_key_is_distinct_from_access_or_service_failure():
    class Client:
        code = "NoSuchKey"

        def get_object(self, **kwargs):
            raise ClientError({"Error": {"Code": self.code}}, "GetObject")

    client = Client()
    artifacts = S3Artifacts("test-bucket", client=client)
    with pytest.raises(FileNotFoundError):
        artifacts.read("0" * 64)
    for code in ["AccessDenied", "InternalError", "SlowDown"]:
        client.code = code
        with pytest.raises(ClientError):
            artifacts.read("0" * 64)


def test_s3_stream_closes_on_success_and_checksum_failure():
    class Client:
        def get_object(self, **kwargs):
            self.body = io.BytesIO(b"actual bytes")
            return {"Body": self.body}

    client = Client()
    artifacts = S3Artifacts("test-bucket", client=client)
    assert artifacts.read(hashlib.sha256(b"actual bytes").hexdigest()) == b"actual bytes"
    assert client.body.closed
    with pytest.raises(ValueError, match="integrity"):
        artifacts.read("0" * 64)
    assert client.body.closed
