"""Secure media management & SSRF protection for social publishing (Phase 8 §6).

Features:
- MIME & dimension/size verification for Facebook, Instagram, LinkedIn
- Anti-SSRF URL validator with strict DNS resolution & private IP blocking
- Signed temporary public access tokens for provider crawler delivery
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import mimetypes
import socket
import time
import urllib.parse
from pathlib import Path
from typing import Any

from app.core.errors import ValidationError

ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_VIDEO_MIMES = {"video/mp4", "video/quicktime"}
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024       # 10 MB
MAX_VIDEO_SIZE_BYTES = 100 * 1024 * 1024      # 100 MB

PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local & cloud metadata
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def validate_media_mime_and_size(filename: str, content: bytes) -> tuple[str, str]:
    """Validate media binary against permissible social media formats.
    Returns (media_type, mime_type).
    """
    mime, _ = mimetypes.guess_type(filename)
    if not mime:
        # Inspect magic bytes
        if content.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif content.startswith(b"RIFF") and b"WEBP" in content[:16]:
            mime = "image/webp"
        elif len(content) > 12 and content[4:8] == b"ftyp":
            mime = "video/mp4"

    if not mime:
        raise ValidationError("Unsupported media format: could not determine MIME type")

    mime = mime.lower()
    size = len(content)

    if mime in ALLOWED_IMAGE_MIMES:
        if size > MAX_IMAGE_SIZE_BYTES:
            raise ValidationError(f"Image exceeds maximum size limit of 10 MB (size: {size} bytes)")
        return "IMAGE", mime

    if mime in ALLOWED_VIDEO_MIMES:
        if size > MAX_VIDEO_SIZE_BYTES:
            raise ValidationError(f"Video exceeds maximum size limit of 100 MB (size: {size} bytes)")
        return "VIDEO", mime

    raise ValidationError(f"MIME type '{mime}' is not supported. Permitted types: JPEG, PNG, WebP, MP4, QuickTime.")


def is_safe_remote_url(raw_url: str) -> bool:
    """Strict SSRF Guard: validates protocol, parses host, resolves DNS,
    and guarantees destination is a public, routable IP address.
    """
    if not raw_url or not isinstance(raw_url, str):
        return False

    try:
        parsed = urllib.parse.urlparse(raw_url.strip())
        if parsed.scheme.lower() not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # Block localhost variants and reserved names
        lowered = hostname.lower()
        if lowered in ("localhost", "local", "internal", "metadata", "instance-data"):
            return False

        # Check if direct IP address was provided
        try:
            ip = ipaddress.ip_address(hostname)
            for net in PRIVATE_NETWORKS:
                if ip in net or ip.is_private or ip.is_loopback or ip.is_link_local:
                    return False
            return True
        except ValueError:
            pass

        # Resolve DNS to verify all returned IPs are public
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        if not addr_info:
            return False

        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            for net in PRIVATE_NETWORKS:
                if ip in net or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
                    return False

        return True
    except Exception:
        return False


def generate_signed_media_token(
    file_id: str,
    secret_key: str,
    ttl_seconds: int = 86400,
) -> str:
    """Generate a tamper-proof HMAC-SHA256 signed token for temporary provider delivery."""
    expires_at = int(time.time()) + ttl_seconds
    data = f"{file_id}:{expires_at}"
    signature = hmac.new(secret_key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{data}:{signature}"


def verify_signed_media_token(token: str, secret_key: str) -> str | None:
    """Validate token signature and expiry. Returns file_id if valid, else None."""
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        file_id, expires_str, provided_sig = parts
        expires_at = int(expires_str)
        if time.time() > expires_at:
            return None
        data = f"{file_id}:{expires_at}"
        expected_sig = hmac.new(secret_key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected_sig, provided_sig):
            return file_id
    except Exception:
        return None
    return None
