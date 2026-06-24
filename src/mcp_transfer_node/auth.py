from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence

from mcp_transfer_node.config import AllowedPeer


def hash_token(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def verify_token(token: str, token_hash: str) -> bool:
    return token_hash.startswith("sha256:") and hmac.compare_digest(
        hash_token(token),
        token_hash,
    )


def authenticate_peer(
    token: str,
    source: str,
    peers: Sequence[AllowedPeer],
) -> AllowedPeer | None:
    return next(
        (
            peer
            for peer in peers
            if peer.enabled and peer.name == source and verify_token(token, peer.token_hash)
        ),
        None,
    )


def verify_web_password(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate, expected)
