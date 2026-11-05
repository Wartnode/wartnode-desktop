"""Public activity and payout rewards independent of private unlock state."""
from datetime import datetime,timezone
import time
from .activity import ActivityStore
from .connection import context,client,history
from . import settings
from .widgets import populate,format_wart


class ActivityActions:
    def refresh_activity(self,network=True):
        if self.activity_busy:return
        owners=[r['address'] for r in self.wallets]
        payout=self.preferences['payout_address'] or None
        selected=list(dict.fromkeys(([payout] if payout else [])+owners))
        self.activity_busy=True
        binding=context(self.directory);generation=self.wallet_generation
        owner=selected[self.activity_turn%len(selected)] if selected else None
        self.activity_turn+=1
        def read():
            store=ActivityStore(self.directory/'activity.db')
            error=None
            try:
                if network and owner:
                    try:
                        rpc=client(self.directory,deadline=time.monotonic()+20)
                        store.sync(history(rpc,settings.chain_directory(self.directory)/'chain.db3'),owner)
                    except (ValueError,OSError) as exc:error=str(exc)
                return store.snapshot(owners,payout),error
            finally:store.close()
        def loaded(result,error):
            self.activity_busy=False
            if binding!=context(self.directory) or generation!=self.wallet_generation or payout!=(self.preferences['payout_address'] or None):
                self.refresh_activity(network=False);return
            if result:self.activity_data,self.activity_error=result
            elif error:self.activity_error=error
            self.render_activity()
        self.submit(read,loaded,quiet=True)

    def refresh_history(self):
        self.refresh_activity(network=self.runtime.ready)
        owners=[r['address'] for r in self.wallets]
        if not owners or self.journal_busy:return
        owner=owners[self.journal_turn%len(owners)];self.journal_turn+=1
        self.journal_busy=True
        def loaded(result,error):
            self.journal_busy=False
            if owner not in [r['address'] for r in self.wallets]:return
            if result:self.payment_rows[owner]=result['rows']
            self.render_activity()
        self.submit(lambda:self.signer.call('reconcile' if self.runtime.ready else 'history',address=owner),loaded,quiet=True)

    def render_activity(self):
        data=self.activity_data or {'rows':[],'rewards':[],'states':{},'payout':None,'reward_complete':False,'reward_count':0,'reward_total':0}
        masked=self.preferences['hide_balances']
        amount=lambda n:'••••••' if masked else format_wart(n)
        stamp=lambda t:datetime.fromtimestamp(t,timezone.utc).strftime('%Y-%m-%d %H:%M')
        labels={r['address']:r['label'] for r in self.wallets}
        rows=[]
        for record in data['rows']:
            kind='Reward' if record['kind']=='reward' else 'Transfer to self' if record['sender'] in labels and record['recipient'] in labels else 'Sent' if record['sender'] in labels else 'Received'
            rows.append((record['timestamp'],[stamp(record['timestamp']),amount(record['amount']),amount(record['fee']),
                record['status'].capitalize(),record['tx_hash'],labels.get(record['owner'],record['owner']),kind,str(record['height'])]))
        for owner,payments in self.payment_rows.items():
            for record in payments:
                if record['status']=='confirmed' and any(r['tx_hash']==record['tx_hash'] and r['height']==record.get('block_height') and r['sender']==owner for r in data['rows']):continue
                rows.append((record['timestamp'],[stamp(record['timestamp']),amount(record['amount_e8']),amount(record['fee_e8']),
                    record['status'].replace('_',' ').capitalize(),record['tx_hash'],labels.get(owner,owner),'Payment',str(record.get('block_height') or '—')]))
        rows.sort(key=lambda r:r[0],reverse=True)
        populate(self.history_table,[r[1] for r in rows[:100]])
        owned=[r['address'] for r in self.wallets]
        complete=all(data['states'].get(owner,{}).get('complete') for owner in owned)
        scope='Current-chain history loaded' if owned and complete else 'History is still loading; totals may be partial' if owned else 'Create or import an address to view activity'
        if not self.runtime.ready:scope+=' · Last known · Offline'
        if self.activity_error:scope+=' · Refresh unavailable'
        self.activity_scope.setText(f'Showing up to 100 recent records · {scope}')
        recent=[]
        for _,row in rows[:4]:recent.append(f'{row[6]} · {row[1]} WART · {row[3]}')
        self.overview_activity.setText('\n\n'.join(recent) if recent else 'No recorded activity.' if owned else 'Create or import an address to view profile activity.')
        self.overview_activity.setToolTip(scope)
        payout=data['payout']
        if payout!=(self.preferences['payout_address'] or None):
            self.reward_summary.setText('Loading reward history for the selected payout address…')
            self.reward_table.setRowCount(0)
            return
        if not payout:self.reward_summary.setText('Choose a payout address to load reward history.')
        else:
            coverage='All-time' if data['reward_complete'] else 'Partial history'
            source=data['states'].get(payout,{}).get('source','Selected connection')
            checked=data['states'].get(payout,{}).get('checked')
            age=max(0,int(time.time()-checked)) if checked else None
            text=f"{coverage} · {data['reward_count']} confirmed blocks · {amount(data['reward_total'])} WART · {source}"
            if len(data['rewards'])==50:text+=' · Latest 50 occurrences shown'
            if age is not None:text+=f' · updated {age}s ago'
            if not self.runtime.ready or self.activity_error:text+=' · Last known'
            self.reward_summary.setText(text)
        populate(self.reward_table,[[r['height'],stamp(r['timestamp']),r['status'].capitalize(),amount(r['amount'])] for r in data['rewards']])
