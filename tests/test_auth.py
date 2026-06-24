from __future__ import annotations

from mcp_transfer_node.auth import authenticate_peer, hash_token, verify_token
from mcp_transfer_node.config import AllowedPeer


def test_hash_and_verify_token() -> None:
    token_hash = hash_token("secret-token")

    assert token_hash.startswith("sha256:")
    assert verify_token("secret-token", token_hash) is True
    assert verify_token("wrong-token", token_hash) is False


def test_authenticate_peer_requires_enabled_peer_and_matching_source() -> None:
    peers = [AllowedPeer(name="server-a", token_hash=hash_token("secret-token"), enabled=True)]

    peer = authenticate_peer("secret-token", "server-a", peers)

    assert peer is not None
    assert peer.name == "server-a"
    assert authenticate_peer("secret-token", "server-b", peers) is None
    assert authenticate_peer("wrong-token", "server-a", peers) is None
