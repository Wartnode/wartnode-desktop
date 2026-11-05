"""Preferences are real validated state, with lossless old mining ceilings."""
from pathlib import Path
import tempfile
import unittest
from wartnode import settings
from wartnode.paths import atomic_json


class DashboardSettingsTests(unittest.TestCase):
    def test_fresh_defaults_and_all_old_presets_preserve_effective_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            fresh = settings.load(directory)
            self.assertEqual((fresh['mining_mode'],fresh['cpu_limit'],fresh['gpu_limit']),('balanced',60,60))
            self.assertEqual(fresh['auto_lock_minutes'],15)
            for mode,limit in [('light',20),('normal',50),('heavy',80),('custom',35)]:
                atomic_json(Path(directory)/'settings.json',dict(schema=1,mining_mode=mode,cpu_limit=limit,gpu_limit=limit))
                value=settings.load(directory)
                self.assertEqual((value['mining_mode'],value['cpu_limit'],value['gpu_limit']),('custom',limit,limit))
                self.assertEqual(value['auto_lock_minutes'],5)

    def test_preferences_survive_restart_and_invalid_values_do_not_replace_them(self):
        with tempfile.TemporaryDirectory() as directory:
            wanted=dict(launch_at_startup=True,close_to_tray=False,start_page='Mine',appearance='system',
                        auto_lock_minutes=60,hide_balances=True,automatic_updates=True,update_channel='stable')
            settings.save(directory,**wanted)
            for key,value in wanted.items():
                self.assertEqual(settings.load(directory)[key],value)
            for key,value in [('launch_at_startup',1),('auto_lock_minutes',True),('appearance','other'),
                              ('start_page','not a page'),('update_channel','untrusted'),('hide_balances','true')]:
                with self.subTest(key=key),self.assertRaises(ValueError):
                    settings.save(directory,**{key:value})
            for key,value in wanted.items():
                self.assertEqual(settings.load(directory)[key],value)

    def test_settings_recovery_preserves_the_new_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            settings.save(directory,hide_balances=True,auto_lock_minutes=0)
            settings.save(directory,start_page='Wallet')
            atomic_json(Path(directory)/'settings.json',dict(schema=2,auto_lock_minutes=-1))
            saved=settings.recover_settings(directory,True)
            self.assertTrue(Path(saved).is_file())
            value=settings.load(directory)
            self.assertTrue(value['hide_balances'])
            self.assertEqual(value['auto_lock_minutes'],0)


if __name__ == '__main__':unittest.main()
