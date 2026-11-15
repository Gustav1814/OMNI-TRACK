"""Unit tests for the security primitives (no DB, no models)."""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.config import settings
from app.security import encryption, jwt_handler
from app.security.client_ip import resolve_client_ip
from app.security.input_safety import (
    UnsafeInputError,
    redact_source,
    resolve_video_file,
    validate_camera_source,
    validate_model_name,
)
from app.security.secrets_check import WeakSecretError, check_secrets_or_raise, collect_secret_problems

STRONG = "Zq3v9Lm2Xr8Tn4Wk7Ys1Hb6Fd0Jp5Gc2-Ue_8Ao"


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "jwt-" + STRONG)
    monkeypatch.setattr(settings, "AES_SECRET_KEY", "aes-" + STRONG)
    monkeypatch.setattr(settings, "AUDIT_HMAC_KEY", "aud-" + STRONG)


# ───────────────────────────── tokens ─────────────────────────────

def test_access_and_refresh_types_are_not_interchangeable(keys):
    access = jwt_handler.create_access_token(7, "admin", token_version=3)
    refresh = jwt_handler.create_refresh_token(7, "jti1", "fam1", jwt_handler.refresh_expiry())
    a = jwt_handler.decode_token(access, jwt_handler.ACCESS)
    assert a["sub"] == "7" and a["role"] == "admin" and a["ver"] == 3 and a["type"] == "access"
    assert jwt_handler.decode_token(refresh, jwt_handler.ACCESS) is None
    assert jwt_handler.decode_token(access, jwt_handler.REFRESH) is None
    assert jwt_handler.decode_token(refresh, jwt_handler.REFRESH)["fam"] == "fam1"


def test_token_lifetimes(keys):
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 15 and settings.REFRESH_TOKEN_EXPIRE_DAYS == 7
    a = jwt_handler.decode_token(jwt_handler.create_access_token(1, "viewer"))
    assert 14 * 60 <= a["exp"] - int(time.time()) <= 15 * 60 + 2


def test_expired_tampered_and_foreign_tokens_rejected(keys, monkeypatch):
    expired = jwt_handler.create_access_token(1, "admin", expires_delta=timedelta(seconds=-5))
    assert jwt_handler.decode_token(expired) is None
    tok = jwt_handler.create_access_token(1, "viewer")
    head, body, sig = tok.split(".")
    assert jwt_handler.decode_token(f"{head}.{body}.{sig[:-2]}AA") is None
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "other-" + STRONG)
    assert jwt_handler.decode_token(tok) is None


def test_password_hashing_handles_long_passwords():
    h = jwt_handler.hash_password("p" * 100 + "1")
    assert jwt_handler.verify_password("p" * 100 + "1", h)
    assert not jwt_handler.verify_password("p" * 100 + "2", h)  # beyond bcrypt's 72-byte cut-off


# ───────────────────────────── encryption ─────────────────────────────

def test_aes_gcm_fresh_nonce_and_auth(keys):
    a, b = encryption.encrypt_bytes(b"crop"), encryption.encrypt_bytes(b"crop")
    assert a != b and a[:12] != b[:12]  # fresh 96-bit nonce each time
    assert encryption.decrypt_bytes(a) == b"crop"
    flipped = a[:-1] + bytes([a[-1] ^ 1])
    with pytest.raises(encryption.DecryptionError):
        encryption.decrypt_bytes(flipped)


def test_dict_roundtrip_and_wrong_key(keys, monkeypatch):
    ct = encryption.encrypt_data({"user": "x", "n": 1})
    assert encryption.decrypt_data(ct) == {"user": "x", "n": 1}
    monkeypatch.setattr(settings, "AES_SECRET_KEY", "different-" + STRONG)
    assert encryption.try_decrypt_data(ct) is None


def test_encrypted_files_bound_to_name(keys, tmp_path):
    p = encryption.write_encrypted_file(tmp_path / "face_1.jpg.enc", b"\xff\xd8jpeg")
    assert b"jpeg" not in p.read_bytes()
    assert (p.stat().st_mode & 0o777) == 0o600
    assert encryption.read_encrypted_file(p) == b"\xff\xd8jpeg"
    moved = tmp_path / "face_2.jpg.enc"
    moved.write_bytes(p.read_bytes())
    with pytest.raises(encryption.DecryptionError):
        encryption.read_encrypted_file(moved)  # swapping crops is detected (AAD = file name)


# ───────────────────────────── secrets ─────────────────────────────

@pytest.mark.parametrize("bad", ["", "0123456789abcdef0123456789abcdef", "your-super-secret-jwt-key-change-in-production",
                                 "Qz7!kp", "a" * 40, "x" * 31])
def test_weak_secrets_refused(bad):
    s = SimpleNamespace(JWT_SECRET_KEY=bad, AES_SECRET_KEY="aes-" + STRONG, AUDIT_HMAC_KEY="aud-" + STRONG,
                        DATABASE_URL=f"postgresql+asyncpg://u:{STRONG}@h/db", ENFORCE_SECRET_STRENGTH=True)
    with pytest.raises(WeakSecretError) as ei:
        check_secrets_or_raise(s)
    assert bad not in str(ei.value) or bad == ""  # never echo the secret


def test_default_db_password_refused_and_strong_config_accepted():
    good = dict(JWT_SECRET_KEY="jwt-" + STRONG, AES_SECRET_KEY="aes-" + STRONG, AUDIT_HMAC_KEY="aud-" + STRONG,
                ENFORCE_SECRET_STRENGTH=True)
    s = SimpleNamespace(**good, DATABASE_URL="postgresql+asyncpg://omnitrack:omnitrack_secret@localhost/db")
    assert any("DATABASE_URL" in p for p in collect_secret_problems(s))
    s = SimpleNamespace(**good, DATABASE_URL=f"postgresql+asyncpg://omnitrack:{STRONG}@localhost/db")
    assert collect_secret_problems(s) == []


def test_reused_key_refused():
    s = SimpleNamespace(JWT_SECRET_KEY=STRONG, AES_SECRET_KEY=STRONG, AUDIT_HMAC_KEY="aud-" + STRONG,
                        DATABASE_URL=f"postgresql://u:{STRONG}@h/db")
    assert any("must differ" in p for p in collect_secret_problems(s))


# ───────────────────────────── input safety ─────────────────────────────

@pytest.fixture
def video_root(tmp_path):
    root = tmp_path / "videos"
    (root / "caviar").mkdir(parents=True)
    (root / "caviar" / "clip.mpg").write_bytes(b"x")
    (tmp_path / "secret.mp4").write_bytes(b"x")
    (root / "link.mp4").symlink_to(tmp_path / "secret.mp4")
    return root


def test_file_sources_confined_to_video_dirs(video_root, tmp_path):
    ok = validate_camera_source("caviar/clip.mpg", video_roots=[video_root])
    assert ok.endswith("caviar/clip.mpg")
    for bad in ("../secret.mp4", str(tmp_path / "secret.mp4"), "link.mp4", "/etc/passwd", "caviar/../../secret.mp4"):
        with pytest.raises(UnsafeInputError):
            validate_camera_source(bad, video_roots=[video_root])
    with pytest.raises(UnsafeInputError):
        resolve_video_file("caviar/clip.mpg\x00.txt", roots=[video_root])


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://127.0.0.1:8000/api/auth/users",       # loopback
    "http://localhost:6379/",                      # Redis via hostname
    "https://10.0.0.5/cam.mjpg",                   # private, not allow-listed
    "http://[::1]/",
    "file:///etc/passwd",
    "gopher://127.0.0.1:6379/_FLUSHALL",
    "ftp://example.com/x.mp4",
])
def test_ssrf_sources_blocked(url):
    with pytest.raises(UnsafeInputError):
        validate_camera_source(url, allowlist=[])


def test_http_allowlist_and_rtsp():
    assert validate_camera_source("https://10.0.0.5/cam.mjpg", allowlist=["10.0.0.0/24"])
    assert validate_camera_source("rtsp://user:pw@192.168.1.10:554/stream").startswith("rtsp://")
    with pytest.raises(UnsafeInputError):
        validate_camera_source("rtsp://169.254.169.254/x")
    with pytest.raises(UnsafeInputError):  # metadata stays blocked even if allow-listed
        validate_camera_source("http://169.254.169.254/", allowlist=["169.254.0.0/16"])
    assert validate_camera_source("0", allow_webcam=True) == "0"
    with pytest.raises(UnsafeInputError):
        validate_camera_source("0", allow_webcam=False)


def test_redaction():
    assert redact_source("rtsp://admin:hunter2@10.0.0.2:554/s") == "rtsp://***@10.0.0.2:554/s"
    assert "hunter2" not in redact_source("http://u:hunter2@cam/x")


def test_model_paths_only_from_weights_dir(tmp_path):
    (tmp_path / "ok.pt").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    assert validate_model_name("ok.pt", weights_dir=tmp_path) == (tmp_path / "ok.pt").resolve()
    for bad in ("../ok.pt", "/tmp/evil.pt", "sub/ok.pt", ".hidden.pt", "notes.txt", "missing.pt", ""):
        with pytest.raises(UnsafeInputError):
            validate_model_name(bad, weights_dir=tmp_path)


# ───────────────────────────── real client IP ─────────────────────────────

def test_forwarded_for_only_from_trusted_proxy():
    # untrusted peer: header ignored (cannot spoof to dodge rate limit / lockout audit)
    assert resolve_client_ip("198.51.100.7", "1.2.3.4", trusted=[]) == "198.51.100.7"
    assert resolve_client_ip("198.51.100.7", "1.2.3.4", trusted=["10.0.0.0/8"]) == "198.51.100.7"
    # trusted proxy: right-most untrusted hop wins (client-supplied left entries ignored)
    assert resolve_client_ip("10.0.0.2", "6.6.6.6, 203.0.113.5", trusted=["10.0.0.0/8"]) == "203.0.113.5"
    assert resolve_client_ip("10.0.0.2", "203.0.113.5, 10.0.0.9", trusted=["10.0.0.0/8"]) == "203.0.113.5"
    assert resolve_client_ip("10.0.0.2", "not-an-ip", trusted=["10.0.0.0/8"]) == "10.0.0.2"
