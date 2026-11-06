"""Stale unlocks cannot overtake a UI lock in the serialized private pipe."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from wartnode.processes import SignerClient


class ProfileClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.client=SignerClient(self.temp.name)
        self.client.call('setup',password='synthetic-client-password')
        self.client.call('lock')
    def tearDown(self):self.client.close();self.temp.cleanup()
    def test_invalid_queued_unlock_never_reaches_the_helper(self):
        with patch.object(self.client,'_exchange',side_effect=AssertionError('Stale request sent')):
            self.assertIsNone(self.client.call('unlock',_valid=lambda:False,password='synthetic-client-password'))
        self.assertTrue(self.client.call('list')['locked'])
    def test_crashed_helper_is_reported_without_waiting_for_a_response_timeout(self):
        self.client.process.kill();self.client.process.wait(5)
        began=time.monotonic()
        with self.assertRaisesRegex(ValueError,'signing helper stopped'):self.client.call('list')
        self.assertLess(time.monotonic()-began,1)
    def test_lock_during_unlock_discards_key_before_releasing_the_pipe(self):
        exchange=self.client._exchange;ready=threading.Event();release=threading.Event()
        valid=[True];results=[]
        def paused(action,args):
            result=exchange(action,args)
            if action=='unlock':
                ready.set()
                if not release.wait(5):raise AssertionError('Fixture did not release unlock')
            return result
        with patch.object(self.client,'_exchange',side_effect=paused):
            thread=threading.Thread(target=lambda:results.append(self.client.call('unlock',
                _valid=lambda:valid[0],password='synthetic-client-password')))
            thread.start()
            try:
                self.assertTrue(ready.wait(5));valid[0]=False;release.set();thread.join(5)
                self.assertFalse(thread.is_alive());self.assertEqual(results,[None])
            finally:release.set();thread.join(5)
        self.assertTrue(self.client.call('list')['locked'])
        self.client.call('unlock',password='synthetic-client-password')
        self.assertFalse(self.client.call('list')['locked'])

    def test_lock_during_vanity_start_or_resume_leaves_job_paused(self):
        self.client.call('unlock',password='synthetic-client-password')
        for action in ('vanity_prepare','vanity_resume'):
            with self.subTest(action=action):
                valid=[True]
                exchange=self.client._exchange
                def invalidated(operation,args):
                    result=exchange(operation,args)
                    if operation==action:valid[0]=False
                    return result
                args={'prefix':'abcdef'} if action=='vanity_prepare' else {'job_id':row['job_id']}
                with patch.object(self.client,'_exchange',side_effect=invalidated):
                    self.assertIsNone(self.client.call(action,_valid=lambda:valid[0],**args))
                self.assertTrue(self.client.call('list')['locked'])
                row=self.client.call('vanity_status')['record']
                self.assertEqual(row['state'],'paused')
                self.client.call('unlock',password='synthetic-client-password')


if __name__=='__main__':unittest.main()
