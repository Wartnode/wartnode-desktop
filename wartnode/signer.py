"""Private JSON-lines signer. No listener or secrets in command-line arguments."""
import copy
import json
from pathlib import Path
import sys
import time
from mnemonic import Mnemonic
from .compat import wallet_utils as crypto
from .profile import ProfileStore
from . import profile_backup, settings
from .rpc import NodeRPC, BroadcastUncertain, NodeError
from . import settlement
from .fees import transaction_fee
from . import proofs


class Signer:
    def __init__(self, directory, rpc=None):
        self.directory = Path(directory)
        self.store = ProfileStore(Path(directory) / "wallet.db")
        self.profile_key = None
        self.override_rpc = rpc  # Internal fixture injection; no IPC operation can change this.
        self.binding = self.connection_context()
        self.rpc = self.binding[0]
        self.address = None
        self.last_active = time.monotonic()
        self.review = None
        self.proof_review = None
        from .vanity_keys import VanityKeys
        self.vanity = VanityKeys(self.store)

    def connection_context(self):
        if self.override_rpc is not None:return (self.override_rpc,0)
        from .connection import context
        return context(self.directory)

    def require_context(self,binding):
        if self.connection_context()!=binding:
            self.lock()
            raise ValueError('The connection changed. Unlock the wallet and review the payment again.')

    def lock(self):
        self.profile_key = None
        self.address = None
        self.review = None
        self.proof_review = None
        return {"locked": True}

    def call(self, action, args):
        binding=self.connection_context()
        if binding!=self.binding:
            self.lock();self.binding=binding;self.rpc=binding[0]
        lock_minutes = settings.load(self.directory).get('auto_lock_minutes', 5)
        if lock_minutes and time.monotonic() - self.last_active > lock_minutes*60:
            self.lock()
        if action not in ("list", "balance", "profile_balances", "history", "reconcile",
                          'vanity_status','vanity_checkpoint','vanity_found','vanity_pause','vanity_fail'):
            self.last_active = time.monotonic()
        if action == "list":
            return {"wallets": self.store.public_wallets(), "address": self.address,
                    "profile_ready": self.store.configured, "locked": self.profile_key is None,
                    "name": self.store.name, "contacts": self.store.contacts()}
        if action == "lock":
            return self.lock()
        if action == 'cancel_review':
            self.review=None
            return {'cancelled':True}
        if action == 'cancel_proof_review':
            if self.proof_review and args.get('token') == self.proof_review['token']:
                self.proof_review = None
            return {'cancelled': True}
        if action == 'setup':
            self.proof_review = None
            self.profile_key = self.store.setup(args['password'], args.get('name', 'Personal'))
            self.address = None
            self.review = None
            return {'profile_ready': True, 'locked': False, 'name': self.store.name}
        if action == 'unlock':
            self.lock()
            key = self.store.unlock(args['password'])
            wallets = self.store.public_wallets()
            address = args.get('address') or (wallets[0]['address'] if wallets else None)
            if address is not None:
                address = crypto.validate_address(address)
                if not any(row['address'] == address for row in wallets):
                    raise ValueError('This address is not in the profile')
            self.profile_key, self.address = key, address
            return {'address': address, 'locked': False}
        if action == 'restore':
            snapshot = profile_backup.load(args['path'], args['password'])
            self.lock()
            count = self.store.restore_profile(snapshot, args['password'])
            return {'added': count, 'locked': True}
        if action == 'profile_balances':
            return self.profile_balances(args, binding)
        if action == 'vanity_status':
            return {'record':self.vanity.info(args.get('job_id'))}
        if action == 'vanity_checkpoint':
            return self.vanity.checkpoint(args['job_id'], args['run_id'], args['checkpoint'])
        if action == 'vanity_found':
            return self.vanity.found(args['job_id'], args['run_id'], args['checkpoint'], args['candidate'])
        if action in ('vanity_pause','vanity_fail'):
            return self.vanity.pause(args['job_id'], args.get('run_id'), args.get('checkpoint'),
                                     args.get('error') if action == 'vanity_fail' else None)
        if action in ('vanity_prepare', 'vanity_resume', 'vanity_discard', 'vanity_finalize'):
            if self.profile_key is None:
                raise ValueError('Unlock your profile first')
            if action == 'vanity_prepare':
                return self.vanity.prepare(self.profile_key, args.get('prefix', ''), args.get('suffix', ''),
                                          args.get('label','Vanity address'), args.get('backend','auto'))
            if action == 'vanity_resume':
                return self.vanity.resume(self.profile_key, args['job_id'], args.get('backend','auto'))
            if action == 'vanity_discard':
                return self.vanity.discard(self.profile_key, args['job_id'])
            result = self.vanity.finalize(self.profile_key, self.store, args['job_id'],
                                         args.get('candidate'), args.get('label', 'Vanity address'))
            self.address = result['address']
            self.review = self.proof_review = None
            return result
        if action in ('select', 'touch', 'edit_address', 'save_contact', 'delete_contact',
                      'change_password', 'create', 'import', 'backup', 'export_private_key') and self.profile_key is None:
            raise ValueError('Unlock your profile first')
        if action == 'select':
            address = crypto.validate_address(args['address'])
            if not self.store.get_wallet_by_address(address):
                raise ValueError('This address is not in the profile')
            self.address, self.review = address, None
            self.proof_review = None
            return {'address': address}
        if action == 'touch':
            return {'locked': False}
        if action == 'edit_address':
            self.store.edit_address(args['address'], args.get('label'), bool(args.get('default', False)))
            return {'saved': True}
        if action == 'save_contact':
            self.store.save_contact(args['name'], args['address'], args.get('previous'))
            return {'saved': True}
        if action == 'delete_contact':
            self.store.delete_contact(args['address'])
            return {'saved': True}
        if action == 'change_password':
            key = self.store.change_password(args['old_password'], args['new_password'])
            self.lock()
            del key
            return {'saved': True, 'locked': True}
        if action in ("create", "import"):
            phrase = None
            if action == "create":
                phrase = Mnemonic("english").generate(strength=256)
                address, key = crypto.wallet_from_seed(Mnemonic.to_seed(phrase, passphrase=""))
            elif args.get("kind") == "phrase":
                phrase_input = args["secret"]
                if not phrase_input.strip():
                    raise ValueError("Enter the complete original recovery phrase")
                if not Mnemonic("english").check(phrase_input) and not args.get("legacy_confirmed"):
                    raise ValueError("The recovery phrase failed its checksum check. Confirm the original phrase before importing.")
                address, key = crypto.wallet_from_seed(Mnemonic.to_seed(phrase_input, passphrase=""))
            else:
                address, key = crypto.wallet_from_pkhex(args["secret"].strip())
            self.store.add_address(self.profile_key, key.to_string().hex(), args.get('label', 'Address'))
            del key
            self.address = address
            self.review = None
            self.proof_review = None
            return {"address": address, "phrase": phrase}
        if action == 'export_private_key':
            key = self.store.unlock(args['password'])
            try:
                if key != self.profile_key:
                    self.lock()
                    raise ValueError('The profile changed. Unlock it again')
                owner, private = self.store.private_key(key, args['address'])
                try:
                    self.require_context(binding)
                    return {'address': owner, 'private_key': private.to_string().hex()}
                finally:
                    del private
            finally:
                del key
        if action == "backup":
            key = self.store.unlock(args['password'])
            if key != self.profile_key:
                self.lock()
                raise ValueError('The profile changed. Unlock it again')
            path = Path(args['path'])
            if path.suffix.lower() != '.wartprofile':
                raise ValueError('Save the backup with the .wartprofile extension')
            profile_backup.save(self.store.snapshot(), args["password"], path)
            del key
            return {"saved": True}
        if action == "history":
            address = crypto.validate_address(args['address'])
            if not self.store.get_wallet_by_address(address):
                raise ValueError('This address is not in the profile')
            return {"rows": self.store.history(address)}
        if action == "reconcile":
            from .node_history import NodeHistory,matches_payment,verify_anchor
            owner = crypto.validate_address(args["address"])
            if not self.store.get_wallet_by_address(owner):
                raise ValueError('This address is not in the profile')
            client = NodeRPC(self.rpc, deadline=time.monotonic()+20)
            head = client.head(require_synced=True)
            client.require_network()
            expected = settlement.refresh(self.store,client,owner,head,lambda:self.require_context(binding))
            from .settings import chain_directory
            from .connection import history as selected_history
            history = selected_history(client,chain_directory(self.directory)/'chain.db3')
            for row in settlement.pending_rows(self.store,owner):
                # Rotate bounded work across the complete journal, including
                # failures, instead of revisiting only its newest 100 entries.
                settlement.checked(self.store,owner,row['tx_hash'])
                if row['block_height']:
                    height=row['block_height']
                    block=client.block(height) if 0<height<=head['height'] else None
                    if block and any(matches_payment(tx,owner,row) for tx in block['body']['transfers']):
                        self.require_context(binding)
                        settlement.confirm(self.store,client,owner,dict(row,status='confirmed',
                            confirmations=max(0,head['height']-height+1)),expected,lambda:self.require_context(binding))
                        continue
                    # Persist the orphan reservation before attempting any further lookup.
                    self.require_context(binding)
                    self.store.save_history([dict(row,status='uncertain',confirmations=0,block_height=0)])
                before,anchor=0,None
                for _ in range(4):
                    page=history.hash_page(row['tx_hash'],before,anchor)
                    candidate=next((entry for entry in page['occurrences']
                        if matches_payment(json.loads(entry['raw']),owner,row)),None)
                    if candidate:
                        block=client.block(candidate['height'])
                        verify_anchor(client,page['anchor'])
                        if any(matches_payment(tx,owner,row) for tx in block['body']['transfers']):
                            self.require_context(binding)
                            settlement.confirm(self.store,client,owner,dict(row,status='confirmed',block_height=candidate['height'],
                                confirmations=max(0,head['height']-candidate['height']+1)),expected,lambda:self.require_context(binding))
                        break
                    if not page['next_before']:break
                    before,anchor=page['next_before'],page['anchor']
            return {'rows':self.store.history(owner)}
        if action in ('review_proof', 'sign_proof'):
            if self.profile_key is None:
                raise ValueError('Unlock your profile first')
            if action == 'review_proof':
                self.proof_review = None
                prepared = proofs.prepare(args['address'], args['kind'], args['content'])
                if not self.store.get_wallet_by_address(prepared['address']):
                    raise ValueError('This address is not in the profile')
                import secrets
                self.proof_review = dict(token=secrets.token_hex(24), prepared=prepared,
                                         binding=binding, created=time.monotonic())
                return dict(token=self.proof_review['token'], **copy.deepcopy(prepared))
            reviewed, self.proof_review = self.proof_review, None
            if (not reviewed or args.get('token') != reviewed['token']
                    or reviewed['binding'] != binding
                    or time.monotonic() - reviewed['created'] > 120):
                raise ValueError('The signature review expired or changed. Review it again.')
            owner, key = self.store.private_key(self.profile_key, reviewed['prepared']['address'])
            try:
                result = proofs.sign(reviewed['prepared'], key)
            finally:
                del key
            self.require_context(binding)
            return result
        if not self.address or self.profile_key is None:
            raise ValueError("Unlock your profile first")
        if action in ("balance", "review", "send"):
            client = NodeRPC(self.rpc)
            head = client.head(require_synced=True)
            client.require_network()
            expected = settlement.refresh(self.store,client,self.address,head,lambda:self.require_context(binding))
            balance = crypto.parse_e8(client.balance(self.address), allow_zero=True)
            settlement.require_head(client,expected)
            reserved = self.store.reserved_amount(self.address)
            if action == "balance":
                self.require_context(binding)
                self.store.update_balance(self.address, crypto.format_e8(balance))
                return {"address": self.address, "balance": balance, "reserved": reserved, "available": max(0, balance-reserved)}
            try:
                fee = transaction_fee(client.minimum_fee())
            except ValueError:
                self.review = None
                raise
            settlement.require_head(client,expected)
            if action == "review":
                amount = max(0, balance-reserved-fee) if args.get("max") else crypto.parse_e8(args["amount"])
                intent = crypto.TransferIntent(crypto.validate_address(args["recipient"].strip()), amount, fee)
                if amount + fee > balance-reserved:
                    raise ValueError("Insufficient available balance after pending payments and fees")
                import secrets
                self.review = {"token": secrets.token_hex(24), "address": self.address, "recipient": intent.recipient,
                    "amount": amount, "fee": fee, "total": amount+fee, "network": "mainnet", "node": self.rpc,
                    "pinHeight": head.get("pinHeight"), "pinHash": head.get("pinHash"), "created": time.monotonic()}
                self.require_context(binding)
                self.review['binding']=binding
                return self.review
            reviewed = self.review
            self.review = None  # A review can authorize only one attempt.
            if (not reviewed or args.get("token") != reviewed["token"] or self.address != reviewed["address"]
                    or reviewed.get('binding')!=binding or reviewed['node']!=self.rpc
                    or time.monotonic()-reviewed["created"] > 90 or fee != reviewed["fee"]
                    or head.get("pinHash") != reviewed["pinHash"] or head.get("pinHeight") != reviewed["pinHeight"]):
                raise ValueError("Payment details changed or the review expired. Review the payment again.")
            owner, key = self.store.private_key(self.profile_key, self.address)
            intent = crypto.TransferIntent(reviewed["recipient"], reviewed["amount"], reviewed["fee"])
            prepared = crypto.prepare_tx(intent.recipient, intent.amount, intent.fee, key, self.rpc)
            del key
            self.require_context(binding)
            after_sign = client.head(require_synced=True)
            settlement.refresh(self.store,client,owner,after_sign,lambda:self.require_context(binding))
            # A reorg can leave the pin unchanged. Re-read confirmed-chain
            # funds and the durable reservation set after signing as well.
            balance = crypto.parse_e8(client.balance(owner),allow_zero=True)
            settlement.require_head(client,(after_sign['height'],after_sign['hash']))
            if (prepared.payload["pinHeight"] != reviewed["pinHeight"]
                    or after_sign.get("pinHash") != reviewed["pinHash"]
                    or after_sign.get("pinHeight") != reviewed["pinHeight"]):
                raise ValueError("The blockchain changed. Review the payment again.")
            self.store.record_outgoing(owner, intent, prepared, self.rpc, balance, "mainnet")
            try:
                self.require_context(binding)
                tx_hash = NodeRPC(self.rpc).broadcast(prepared.payload, prepared.tx_hash)
            except BroadcastUncertain:
                self.store.set_tx_status(owner, prepared.tx_hash, "uncertain")
                return {"tx_hash": prepared.tx_hash, "status": "uncertain"}
            except NodeError:
                self.store.set_tx_status(owner, prepared.tx_hash, "rejected")
                raise
            self.store.set_tx_status(owner, tx_hash, "pending")
            return {"tx_hash": tx_hash, "status": "pending"}
        raise ValueError("Unknown signer operation")

    def profile_balances(self, args, binding):
        """Public reads stay bounded and never decrypt a private key."""
        offset, limit = args.get('offset', 0), args.get('limit', 20)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 25:
            raise ValueError('Invalid balance page')
        wallets = self.store.public_wallets()
        selected = wallets[offset:offset+limit]
        if not selected:
            return {'rows': [], 'total': len(wallets), 'next_offset': None, 'source': self.rpc}
        client = NodeRPC(self.rpc, deadline=time.monotonic()+20)
        client.require_network()
        head = client.head(require_synced=True)
        rows = []
        for record in selected:
            owner = record['address']
            try:
                expected = settlement.refresh(self.store, client, owner, head,
                                              lambda: self.require_context(binding))
                balance = crypto.parse_e8(client.balance(owner), allow_zero=True)
                settlement.require_head(client, expected)
                self.require_context(binding)
                reserved = self.store.reserved_amount(owner)
                self.store.update_balance(owner, crypto.format_e8(balance))
                rows.append(dict(address=owner, balance=balance, reserved=reserved,
                                 available=max(0, balance-reserved)))
            except ValueError:
                self.require_context(binding)
                rows.append(dict(address=owner, error='Balance unavailable'))
        self.require_context(binding)
        return {'rows': rows, 'total': len(wallets), 'source': self.rpc,
                'next_offset': offset+limit if offset+limit < len(wallets) else None}


def serve(directory):
    signer = Signer(directory)
    for line in sys.stdin.buffer:
        response = {"id": None}
        request = None
        try:
            if len(line) > 1024 * 1024:
                raise ValueError("Request exceeds limit")
            request = json.loads(line)
            response["id"] = request["id"]
            response["result"] = signer.call(request["action"], request.get("args", {}))
        except Exception as exc:
            # Controlled messages only: never include a request or traceback.
            response["error"] = str(exc)[:300] if isinstance(exc, ValueError) else "Wallet operation failed"
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
        # Do not retain the last exported key or password while the pipe is idle.
        response.clear()
        request = None
        del line
    signer.lock()
    signer.store.con.close()
