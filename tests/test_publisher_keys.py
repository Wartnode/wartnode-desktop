import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from maintainer import keys


class PublisherKeysTests(unittest.TestCase):
    def test_encrypted_portable_recovery_rejects_wrong_password_and_tampering(self):
        original=keys.generate();password='synthetic-key-recovery-password'
        backup=keys.recovery(original,password)
        self.assertEqual(keys.public_record(keys.recover(backup,password)),keys.public_record(original))
        self.assertNotEqual(keys.public_record(original)['keys']['release'],keys.public_record(original)['keys']['update'])
        for key in original.values():
            self.assertNotIn(key.private_bytes_raw().hex().encode(),backup)
            self.assertNotIn(key.private_bytes_raw(),backup)
        with self.assertRaises(ValueError):keys.recover(backup,'incorrect-password')
        value=json.loads(backup);cipher=bytearray(base64.b64decode(value['ciphertext']));cipher[-1]^=1
        value['ciphertext']=base64.b64encode(cipher).decode()
        with self.assertRaises(ValueError):keys.recover(json.dumps(value).encode(),password)
        value['kdf']='scrypt-1-1-1'
        with self.assertRaises(ValueError):keys.recover(json.dumps(value).encode(),password)

    @unittest.skipUnless(os.name=='nt','Windows current-user DPAPI')
    def test_current_user_store_and_independent_recovered_store_sign_identically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);original=keys.generate();keys.save(root/'primary',original)
            protected=(root/'primary/publisher.dpapi').read_bytes()
            self.assertNotIn(keys.encode(original),protected)
            loaded=keys.load(root/'primary');keys.prove(loaded)
            recovered=keys.recover(keys.recovery(original,'synthetic-key-recovery-password'),'synthetic-key-recovery-password')
            keys.save(root/'recovered',recovered);restored=keys.load(root/'recovered');keys.prove(restored)
            self.assertEqual(keys.public_record(loaded),keys.public_record(restored))
            for role in keys.ROLES:self.assertEqual(loaded[role].sign(b'recovery rehearsal'),restored[role].sign(b'recovery rehearsal'))
            with self.assertRaises(ValueError):keys.save(root/'primary',keys.generate())

    def test_recovery_limits_are_checked_before_expensive_derivation(self):
        with self.assertRaises(ValueError):keys.recover(b'x'*(keys.MAX_KEYS+1),'password')
        with self.assertRaises(ValueError):keys.recovery(keys.generate(),'short')
