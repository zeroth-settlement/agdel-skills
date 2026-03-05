"""Cryptographic utilities for AGDEL commit-reveal protocol.

Handles commitment hash computation, EIP-191 signing, and X25519
encrypted delivery for the AGDEL marketplace.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from eth_account import Account
from web3 import Web3

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_ENCRYPTION_KEY_FILE = _DATA_DIR / "maker_encryption_key.json"


def scale_price(price: float) -> int:
    """Scale a human-readable price to 1e8 integer for the AGDEL protocol."""
    return int(round(price * 10**8))


def confidence_to_cost(
    confidence: float, min_cost: float = 0.01, max_cost: float = 0.20
) -> float:
    """Map confidence (0-1) to listing price. Higher confidence = higher price."""
    clamped = max(0.0, min(1.0, confidence))
    return round(min_cost + clamped * (max_cost - min_cost), 2)


def compute_commitment_hash(
    maker: str,
    asset: str,
    target_price_scaled: int,
    direction_int: int,
    expiry_time: int,
    salt: bytes,
) -> bytes:
    """keccak256(abi.encodePacked(maker, asset, targetPrice, direction, expiryTime, salt))"""
    return Web3.solidity_keccak(
        ["address", "string", "uint256", "uint8", "uint256", "bytes32"],
        [
            Web3.to_checksum_address(maker),
            asset,
            target_price_scaled,
            direction_int,
            expiry_time,
            salt,
        ],
    )


def prepare_signal(
    private_key: str,
    asset: str,
    target_price: float,
    direction: str,
    duration_seconds: int,
) -> dict[str, Any]:
    """Compute commitment hash for a signal prediction.

    Returns a dict with commitment_hash, salt, and all parameters needed
    for listing creation and later reveal.
    """
    salt = secrets.token_bytes(32)
    # +10s buffer so the signal doesn't expire mid-creation
    expiry_time = int(time.time()) + duration_seconds + 10
    account = Account.from_key(private_key)
    target_price_scaled = scale_price(target_price)
    direction_int = 0 if direction.lower() == "long" else 1

    commitment_hash = compute_commitment_hash(
        maker=account.address,
        asset=asset,
        target_price_scaled=target_price_scaled,
        direction_int=direction_int,
        expiry_time=expiry_time,
        salt=salt,
    )

    return {
        "commitment_hash": "0x" + commitment_hash.hex(),
        "salt_hex": "0x" + salt.hex(),
        "expiry_time": expiry_time,
        "target_price_scaled": target_price_scaled,
        "direction_int": direction_int,
        "maker": account.address.lower(),
    }



def load_or_create_encryption_keypair() -> dict[str, str]:
    """Load or generate a persistent X25519 keypair for encrypted delivery.

    Returns dict with private_key_b64, public_key_b64, algorithm.
    """
    if _ENCRYPTION_KEY_FILE.exists():
        try:
            key_data = json.loads(_ENCRYPTION_KEY_FILE.read_text(encoding="utf-8"))
            if "public_key_b64" in key_data and "private_key_b64" in key_data:
                return key_data
        except Exception:
            pass

    private_key = X25519PrivateKey.generate()
    priv_bytes = private_key.private_bytes_raw()
    pub_bytes = private_key.public_key().public_bytes_raw()
    key_data = {
        "private_key_b64": base64.b64encode(priv_bytes).decode(),
        "public_key_b64": base64.b64encode(pub_bytes).decode(),
        "algorithm": "x25519-aes256gcm",
    }
    _ENCRYPTION_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ENCRYPTION_KEY_FILE.write_text(
        json.dumps(key_data, indent=2), encoding="utf-8"
    )
    os.chmod(_ENCRYPTION_KEY_FILE, 0o600)
    print("[crypto] Generated new encryption keypair", flush=True)
    return key_data


def encrypt_for_buyer(plaintext: bytes, buyer_pubkey_b64: str) -> dict[str, str]:
    """Encrypt plaintext using X25519-ECDH + HKDF-SHA256 + AES-256-GCM.

    Returns dict with ephemeral_pubkey_b64, nonce_b64, ciphertext_b64.
    """
    buyer_pubkey = X25519PublicKey.from_public_bytes(
        base64.b64decode(buyer_pubkey_b64)
    )
    ephemeral_key = X25519PrivateKey.generate()
    shared_secret = ephemeral_key.exchange(buyer_pubkey)

    derived_key = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=None,
        info=b"agdel-signal-delivery",
    ).derive(shared_secret)

    nonce = os.urandom(12)
    ciphertext = AESGCM(derived_key).encrypt(nonce, plaintext, None)

    ephemeral_pub_bytes = ephemeral_key.public_key().public_bytes_raw()
    return {
        "ephemeral_pubkey_b64": base64.b64encode(ephemeral_pub_bytes).decode(),
        "nonce_b64": base64.b64encode(nonce).decode(),
        "ciphertext_b64": base64.b64encode(ciphertext).decode(),
    }
