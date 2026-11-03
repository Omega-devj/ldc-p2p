"""Chiffrement au repos des champs sensibles de la base locale."""
from __future__ import annotations

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305


class FieldCipher:
    def __init__(self, key: bytes):
        self._aead = ChaCha20Poly1305(key)

    def encrypt(self, data: bytes, aad: bytes = b"") -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, data, aad)

    def decrypt(self, blob: bytes, aad: bytes = b"") -> bytes:
        try:
            return self._aead.decrypt(blob[:12], blob[12:], aad)
        except InvalidTag as e:
            raise ValueError("Donnée locale corrompue ou clé incorrecte") from e
