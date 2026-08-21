import os
import unittest
from unittest.mock import patch

from archive_manager.core.encryption import ENCRYPTED_PREFIX, decrypt_bytes, encrypt_bytes, generate_key


class EncryptionTest(unittest.TestCase):
    def test_key_round_trip(self):
        key = generate_key()
        with patch.dict(os.environ, {"ARCHIVE_ENCRYPTION_KEY": key}, clear=False):
            encrypted = encrypt_bytes(b"sensitive metadata")
            self.assertTrue(encrypted.startswith(ENCRYPTED_PREFIX))
            self.assertEqual(decrypt_bytes(encrypted), b"sensitive metadata")

    def test_plaintext_compatibility_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(encrypt_bytes(b"legacy"), b"legacy")
            self.assertEqual(decrypt_bytes(b"legacy"), b"legacy")

    def test_encrypted_data_requires_key(self):
        key = generate_key()
        with patch.dict(os.environ, {"ARCHIVE_ENCRYPTION_KEY": key}, clear=False):
            encrypted = encrypt_bytes(b"private")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                decrypt_bytes(encrypted)


if __name__ == "__main__":
    unittest.main()