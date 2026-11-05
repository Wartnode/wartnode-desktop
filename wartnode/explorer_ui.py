"""Rolling block presentation and validated external explorer navigation."""
from datetime import datetime, timezone
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from .widgets import populate, format_wart


def explorer_url(height=None):
    base='https://wartnode.com/mainnet'
    if height is None:return base
    if isinstance(height,str) and height.isascii() and height.isdigit() and len(height)<=10:
        height=int(height)
    if type(height) is not int or not 1<=height<=0xffffffff:
        raise ValueError('Invalid external block height')
    return base+'/blocks/'+str(height)


class ExplorerActions:
    def open_web_explorer(self,height=None):
        # Qt clicked(bool) is not a height.
        if type(height) is bool:height=None
        try:url=explorer_url(height)
        except ValueError as error:
            self.notice.setText(str(error));return
        if not QDesktopServices.openUrl(QUrl(url)):
            self.notice.setText('Could not open your default browser.')

    def recent_block_link(self,row,column):
        item=self.recent_table.item(row,0)
        if item and column==0:self.open_web_explorer(item.text())

    def inspect_recent_block(self,row,column=0):
        if not 0<=row<len(self.rolling_rows):return
        block=self.rolling_rows[row]
        self.explorer_selected=(block['height'],block['hash'])
        self.search_explorer(query_override=block['hash'],show_results=False)

    def toggle_blocks_paused(self):
        self.blocks_paused=not self.blocks_paused
        self.pause_blocks.setText('Resume' if self.blocks_paused else 'Pause')
        if not self.blocks_paused:self.display_recent_blocks(self.latest_block_rows)

    def show_latest_blocks(self):
        self.explorer_generation+=1
        self.explorer_page=None
        self.search.clear()
        self.explorer_views.setCurrentIndex(0)
        self.older_results.setEnabled(False)

    def display_recent_blocks(self,rows):
        self.latest_block_rows=list(rows)[:25]
        if self.blocks_paused:return
        scroll=self.recent_table.verticalScrollBar().value()
        selected=self.explorer_selected
        self.rolling_rows=self.latest_block_rows
        hidden=self.preferences['hide_balances']
        populate(self.recent_table,[[r['height'],datetime.fromtimestamp(r['timestamp'],timezone.utc).strftime('%H:%M:%S'),
            r.get('transactions','—'),r['hash'],r.get('miner','—'),
            '••••••' if hidden else format_wart(r['reward']) if r.get('reward') is not None else '—'] for r in self.rolling_rows])
        self.recent_table.clearSelection()
        if selected:
            for i,row in enumerate(self.rolling_rows):
                if (row['height'],row['hash'])==selected:self.recent_table.selectRow(i)
                elif row['height']==selected[0] and self.explorer_views.currentIndex()==0:
                    self.explorer_details.setPlainText('The selected block was replaced on the current chain. Select a block to refresh its details.')
        self.recent_table.verticalScrollBar().setValue(scroll)
        self.latest_blocks_status.setText(('Cached blocks · Offline' if rows else 'Connect a node to load the latest blocks.')
            if not self.runtime.rpc_available else 'Loading the latest 25 blocks…' if len(rows)<25 else 'Newest blocks first')
