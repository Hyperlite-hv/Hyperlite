"""The mirror check must notice what makes apt fail: index files that do not match Release."""

import gzip
import hashlib
import subprocess
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-apt-mirror.sh"


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _entry(digest_source: bytes, path: str) -> str:
    return f" {hashlib.sha256(digest_source).hexdigest()} {len(digest_source)} {path}"


def _build_mirror(root: Path, versions=("2026.01.01.0000",)) -> bytes:
    index = root / "dists" / "stable" / "main" / "binary-amd64"
    index.mkdir(parents=True)
    packages = "".join(f"Package: hyperlite\nVersion: {v}\n\n" for v in versions).encode()
    packages_gz = gzip.compress(packages)
    (index / "Packages").write_bytes(packages)
    (index / "Packages.gz").write_bytes(packages_gz)
    release = "Suite: stable\nSHA256:\n" + "\n".join(
        [_entry(packages, "main/binary-amd64/Packages"), _entry(packages_gz, "main/binary-amd64/Packages.gz")]
    )
    (root / "dists" / "stable" / "Release").write_text(release + "\n")
    return packages_gz


@pytest.fixture()
def serve(tmp_path):
    servers = []

    def _serve(root: Path) -> str:
        server = HTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(root)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _serve
    for server in servers:
        server.shutdown()


def _check(url, *extra):
    return subprocess.run(["bash", str(SCRIPT), "--url", url, *extra], capture_output=True, text=True, timeout=60)


def test_a_consistent_mirror_passes(tmp_path, serve):
    _build_mirror(tmp_path)
    result = _check(serve(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_an_index_that_does_not_match_release_is_reported(tmp_path, serve):
    _build_mirror(tmp_path)
    # What happened in production: Packages.gz moved on while the signed Release stayed old.
    (tmp_path / "dists/stable/main/binary-amd64/Packages.gz").write_bytes(
        gzip.compress(b"Package: x\nVersion: 9\n\n" * 20)
    )
    result = _check(serve(tmp_path))
    assert result.returncode == 1
    assert "Packages.gz is out of sync" in result.stderr


def test_a_missing_expected_version_is_reported_as_stale(tmp_path, serve):
    _build_mirror(tmp_path, versions=("2026.01.01.0000",))
    result = _check(serve(tmp_path), "--expect-version", "2026.02.02.0000")
    assert result.returncode == 2
    assert "2026.02.02.0000" in result.stderr
    assert _check(serve(tmp_path), "--expect-version", "2026.01.01.0000").returncode == 0


def test_an_unreachable_mirror_is_reported(tmp_path):
    result = _check("http://127.0.0.1:9")
    assert result.returncode == 3
    assert "cannot fetch" in result.stderr


def test_the_alert_webhook_receives_the_failure(tmp_path, serve):
    received = []

    class Hook(SimpleHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    hook = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=hook.serve_forever, daemon=True).start()
    try:
        _build_mirror(tmp_path)
        (tmp_path / "dists/stable/main/binary-amd64/Packages").write_bytes(b"tampered\n")
        env_url = f"http://127.0.0.1:{hook.server_address[1]}/hook"
        result = subprocess.run(
            ["bash", str(SCRIPT), "--url", serve(tmp_path)],
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": "/usr/bin:/bin", "HYPERLITE_ALERT_WEBHOOK": env_url},
        )
        assert result.returncode == 1
        assert received and "out of sync" in received[0]
    finally:
        hook.shutdown()
