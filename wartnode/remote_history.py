"""Bounded hosted history discovery, resolved against canonical node blocks."""
import json
import re
import time
import urllib.request
from urllib.parse import urlencode
from .connection import EXPLORER_ENDPOINT
from .node_history import NodeHistory, MAINNET_BLOCK_ONE, PAGE_SIZE, transaction_row, verify_anchor
from .rpc import NodeError, HEX64
from .network import open_response


class RemoteHistory(NodeHistory):
    def __init__(self, client):
        self.client, self.chain_path = client, None

    def block_height(self, block_hash):
        if not HEX64.fullmatch(str(block_hash)):raise NodeError('Invalid block hash')
        try:block = self.client.block(block_hash)
        except NodeError as error:
            if error.code == 57:return None
            raise
        if block.get('header',{}).get('hash')!=block_hash or type(block.get('height')) is not int:
            raise NodeError('The service returned a different block')
        return block['height']

    def index_page(self, tx_hash, cursor):
        self.client.check_cancelled()
        query={'hash':tx_hash,'limit':str(PAGE_SIZE)}
        if cursor:query['cursor']=cursor
        request=urllib.request.Request(EXPLORER_ENDPOINT+'/transactions?'+urlencode(query),
                                      headers={'Accept':'application/json'})
        try:
            with open_response(self.client.opener,request,self.client.deadline,self.client.cancel) as response:
                if response.status!=200:raise ValueError()
                raw=response.read_bounded(4*1024*1024)
                result=json.loads(raw)
            meta=result['meta'];data=result['data']
            if (not isinstance(meta,dict) or not isinstance(data,dict)
                    or meta['network']!='mainnet' or meta['epoch']!='mainnet:'+MAINNET_BLOCK_ONE
                    or meta['enabled'] is not True or not isinstance(data['items'],list)
                    or len(data['items'])>PAGE_SIZE):raise ValueError()
            return data,meta
        except (OSError,ValueError,KeyError,TypeError) as error:
            raise NodeError('Hosted transaction history is unavailable. Try again later.') from error

    def hash_page(self, tx_hash, before=0, anchor=None):
        if not HEX64.fullmatch(str(tx_hash)) or (before and (
                not isinstance(before,str) or len(before)>2048 or not re.fullmatch('[A-Za-z0-9_-]+',before))):
            raise NodeError('Invalid transaction page request')
        current=self.anchor()
        if anchor:verify_anchor(self.client,anchor)
        data,meta=self.index_page(tx_hash,before)
        cursor=data.get('nextCursor') or 0
        if cursor and (not isinstance(cursor,str) or len(cursor)>2048
                       or not re.fullmatch('[A-Za-z0-9_-]+',cursor) or cursor==before):
            raise NodeError('Invalid hosted history cursor')
        rows=[];blocks={};seen=set()
        for item in data['items']:
            if not isinstance(item,dict):raise NodeError('Invalid hosted transaction occurrence')
            height,position=item.get('height'),item.get('position')
            if (item.get('hash')!=tx_hash or type(height) is not int or not 0<height<=current['height']
                    or type(position) is not int or position<0 or (height,position) in seen):
                raise NodeError('Invalid hosted transaction occurrence')
            seen.add((height,position))
            if height not in blocks:blocks[height]=self.client.block(height)
            block=blocks[height]
            entries=block['body']['rewards']+block['body']['transfers']
            if position>=len(entries) or entries[position].get('txHash')!=tx_hash:
                raise NodeError('Hosted history does not match the node block')
            verify_anchor(self.client,{'height':height,'hash':block['header']['hash']})
            rows.append(transaction_row(entries[position],height,position,block['header']['hash']))
        verify_anchor(self.client,anchor or current)
        complete=meta.get('coverage',{}).get('complete') is True and meta.get('stale') is False
        return {'type':'transactions','occurrences':rows,'next_before':cursor,'anchor':anchor or current,
                'source':'hosted node and explorer','complete':complete and not cursor}

    def address_page(self,*args,**kwargs):
        result=super().address_page(*args,**kwargs)
        result['source']='hosted node'
        return result
