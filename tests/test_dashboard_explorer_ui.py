import unittest
from unittest.mock import patch
import test_ui
from wartnode.explorer_ui import explorer_url


class ExplorerDashboardTests(unittest.TestCase):
    setUp=test_ui.NativeUITests.setUp
    tearDown=test_ui.NativeUITests.tearDown

    def rows(self,height):
        return [dict(height=n,hash=f'{n:064x}',timestamp=1700000000+n,transactions=1,miner='Fixture',reward=150000000) for n in range(height,height-25,-1)]

    def test_rolling_updates_preserve_selection_and_pause_without_a_live_badge(self):
        window=self.window;window.timer.stop()
        window.display_recent_blocks(self.rows(100))
        with patch.object(window,'search_explorer') as search:
            window.inspect_recent_block(4)
        self.assertEqual(window.explorer_selected,(96,f'{96:064x}'))
        window.display_recent_blocks(self.rows(101))
        self.assertEqual(window.recent_table.currentRow(),5)
        self.assertEqual(window.recent_table.rowCount(),25)
        window.toggle_blocks_paused();window.display_recent_blocks(self.rows(102))
        self.assertEqual(window.recent_table.item(0,0).text(),'101')
        window.toggle_blocks_paused()
        self.assertEqual(window.recent_table.item(0,0).text(),'102')
        self.assertNotIn('Live',window.latest_blocks_status.text())
        replacement=self.rows(102);replacement[6]['hash']='ee'*32
        window.display_recent_blocks(replacement)
        self.assertIn('replaced',window.explorer_details.toPlainText())

    def test_double_click_opens_only_height_using_validated_route(self):
        window=self.window;window.timer.stop();window.display_recent_blocks(self.rows(100))
        with patch('wartnode.explorer_ui.QDesktopServices.openUrl',return_value=True) as opened:
            window.recent_block_link(0,1);opened.assert_not_called()
            window.recent_block_link(0,0)
            self.assertEqual(opened.call_args.args[0].toString(),'https://wartnode.com/mainnet/blocks/100')
        for value in (0,True,'../3','3?x','٣',2**32):
            with self.subTest(value=value),self.assertRaises(ValueError):explorer_url(value)


if __name__=='__main__':unittest.main()
