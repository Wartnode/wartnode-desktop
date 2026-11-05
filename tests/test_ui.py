import os
from pathlib import Path
import tempfile
import time
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QAbstractSlider
from PySide6.QtTest import QTest
from wartnode.paths import data_root
from wartnode.ui import Window
from wartnode.theme import apply

APP=QApplication.instance() or QApplication([])
apply(APP)


def until(condition,seconds=12):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        APP.processEvents()
        if condition():return
        QTest.qWait(20)
    raise AssertionError('Native UI operation did not finish')


class NativeUITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.window=Window(data_root(self.temp.name),autostart=False)
        self.window.show()
        until(lambda:not self.window.tasks)

    def tearDown(self):
        self.window.timer.stop()
        self.window.exiting=True
        self.window.signer.close()
        self.window.pool.waitForDone(30000)
        self.window.tray.hide()
        self.window.close()
        APP.processEvents()
        self.temp.cleanup()

    def test_six_native_screens_mainnet_only_and_no_cpu_only_option(self):
        self.assertEqual(self.window.stack.count(),6)
        for index in range(6):
            self.window.nav_buttons[index].click()
            APP.processEvents()
            self.assertEqual(self.window.stack.currentIndex(),index)
        options=[self.window.backend.itemData(i) for i in range(self.window.backend.count())]
        self.assertNotIn('cpu',options)
        self.assertFalse(self.window.start_mining.isEnabled())
        self.assertFalse(self.window.review_button.isEnabled())

    def test_log_views_follow_newest_preserve_manual_scroll_and_resume_at_bottom(self):
        for page,view in ((1,self.window.node_log),(3,self.window.miner_log)):
            self.window.navigate(page)
            APP.processEvents()
            lines=[f'Log entry {index:03d}' for index in range(220)]
            view.update_lines(lines)
            APP.processEvents()
            scroll=view.verticalScrollBar()
            self.assertGreater(scroll.maximum(),0)
            self.assertEqual(scroll.value(),scroll.maximum())
            lines.append('Log entry 220')
            view.update_lines(lines)
            self.assertEqual(scroll.value(),scroll.maximum())
            for _ in range(3):
                scroll.triggerAction(QAbstractSlider.SliderAction.SliderPageStepSub)
            anchor=view.firstVisibleBlock().text()
            lines.append('Log entry 221')
            view.update_lines(lines)
            self.assertEqual(view.firstVisibleBlock().text(),anchor)
            view.update_lines(lines)
            self.assertEqual(view.firstVisibleBlock().text(),anchor)
            lines.extend(f'Log entry {index:03d}' for index in range(222,265))
            view.update_lines(lines[-250:])
            self.assertEqual(view.document().blockCount(),250)
            self.assertEqual(view.firstVisibleBlock().text(),anchor)
            self.assertLess(scroll.value(),scroll.maximum())
            scroll.triggerAction(QAbstractSlider.SliderAction.SliderToMaximum)
            lines.append('Log entry 265')
            view.update_lines(lines[-250:])
            self.assertEqual(scroll.value(),scroll.maximum())
            self.assertTrue(view.toPlainText().endswith('Log entry 265'))
            view.update_lines(lines[-30:])
            self.assertEqual(view.toPlainText(),'\n'.join(lines[-30:]))
            view.update_lines(['Restarted process'])
            self.assertEqual(view.toPlainText(),'Restarted process')

    def test_hidden_log_pages_and_resize_keep_tail_without_overriding_manual_scroll(self):
        for page,view in ((1,self.window.node_log),(3,self.window.miner_log)):
            self.window.navigate(0)
            APP.processEvents()
            lines=[f'Background entry {index:03d}' for index in range(240)]
            view.update_lines(lines)
            APP.processEvents()
            self.window.navigate(page)
            APP.processEvents()
            scroll=view.verticalScrollBar()
            self.assertEqual(scroll.value(),scroll.maximum())
            self.window.resize(1130,740)
            APP.processEvents()
            self.assertEqual(scroll.value(),scroll.maximum())
            scroll.triggerAction(QAbstractSlider.SliderAction.SliderPageStepSub)
            anchor=view.firstVisibleBlock().text()
            self.assertFalse(view.following)
            self.window.navigate(0)
            lines.append('Background entry 240')
            view.update_lines(lines)
            APP.processEvents()
            self.window.navigate(page)
            APP.processEvents()
            self.assertEqual(view.firstVisibleBlock().text(),anchor)
            self.assertLess(scroll.value(),scroll.maximum())
            scroll.triggerAction(QAbstractSlider.SliderAction.SliderToMaximum)
            self.window.navigate(0)
            lines.append('Background entry 241')
            view.update_lines(lines)
            self.window.navigate(page)
            APP.processEvents()
            self.assertEqual(scroll.value(),scroll.maximum())

    def test_signer_transport_and_receive_qr_with_synthetic_wallet(self):
        secret='00'*31+'04'
        self.window.signer.call('setup',password='synthetic-password')
        result=self.window.signer.call('import',kind='private',secret=secret,password='synthetic-password')
        self.window.select_unlocked(result['address'])
        until(lambda:not self.window.tasks)
        self.assertEqual(self.window.receive_address.text(),result['address'])
        self.assertFalse(self.window.qr.pixmap().isNull())
        self.assertNotIn('secret',self.window.signer.call('list'))
        self.window.lock_wallet()
        until(lambda:not self.window.tasks)
        self.assertEqual(self.window.receive_address.text(),result['address'])
        self.assertIsNone(self.window.signer.call('list')['address'])

    def test_palette_screens_render_without_clipped_minimum_layout(self):
        directory=Path(__file__).resolve().parents[1]/'work/test-screenshots'
        directory.mkdir(parents=True,exist_ok=True)
        for index,name in enumerate(('overview','node','wallet','mine','explorer','settings')):
            self.window.navigate(index)
            APP.processEvents()
            self.assertTrue(self.window.grab().save(str(directory/(name+'.png'))))
        desktop=APP.primaryScreen().availableGeometry()
        self.assertLessEqual(self.window.width()+16,desktop.width())
        self.assertLessEqual(self.window.height()+32,desktop.height())

    def test_windows_session_lock_invalidates_wallet_and_payment_context(self):
        if os.name != 'nt':self.skipTest('Windows native message')
        import ctypes
        from ctypes import wintypes
        self.window.signer.call('setup',password='synthetic-password')
        result=self.window.signer.call('import',kind='private',secret='00'*31+'04',password='synthetic-password')
        self.window.select_unlocked(result['address'])
        until(lambda:not self.window.tasks)
        msg=wintypes.MSG()
        msg.message,msg.wParam=0x02B1,7
        self.window.nativeEvent(b'windows_generic_MSG',ctypes.addressof(msg))
        until(lambda:not self.window.tasks)
        self.assertIsNone(self.window.address)
        self.assertIsNone(self.window.signer.call('list')['address'])

    def test_signer_autolock_and_offline_balance_are_reflected_in_ui(self):
        self.window.address='synthetic-address'
        self.window.balance_loaded(None,'Unlock your profile first')
        until(lambda:not self.window.tasks)
        self.assertIsNone(self.window.address)
        self.window.address='synthetic-address'
        self.window.wallets=[{'address':'synthetic-address'}]
        self.window.profile_balance=None
        self.window.update_state({'state':'Stopped'})
        self.assertEqual(self.window.summaries['Available WART'].text(),'Unavailable')


if __name__=='__main__':unittest.main()
