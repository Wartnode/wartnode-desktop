"""Private JSON-lines signer. No listener or secrets in command-line arguments."""
import json
from pathlib import Path
import sys
import time
from mnemonic import Mnemonic
from .compat import WalletDB, wallet_utils as crypto, wallet_backup
from .rpc import NodeRPC, BroadcastUncertain, NodeError
from . import settlement


class Signer:
    def __init__(self, directory, rpc=None):
        self.directory = Path(directory)
        self.store = WalletDB(Path(directory) / "wallet.db")
        self.override_rpc = rpc  # Internal fixture injection; no IPC operation can change this.
        self.binding = self.connection_context()
        self.rpc = self.binding[0]
        self.address = None
        self.last_active = time.monotonic()
        self.review = None

    def connection_context(self):
        if self.override_rpc is not None:return (self.override_rpc,0)
        from .connection import context
        return context(self.directory)

    def require_context(self,binding):
        if self.connection_context()!=binding:
            self.lock()
            raise ValueError('The connection changed. Unlock the wallet and review the payment again.')

    def lock(self):
        self.address = None
        self.review = None
        return {"locked": True}

    def call(self, action, args):
        binding=self.connection_context()
        if binding!=self.binding:
            self.lock();self.binding=binding;self.rpc=binding[0]
        if time.monotonic() - self.last_active > 300:
            self.lock()
        if action not in ("list", "balance", "history", "reconcile"):
            self.last_active = time.monotonic()
        if action == "list":
            return {"wallets": self.store.public_wallets(), "address": self.address}
        if action == "lock":
            return self.lock()
        if action in ("create", "import"):
            crypto.validate_password(args["password"])
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
            encrypted, salt = crypto.encrypt_pk(key.to_string().hex(), args["password"])
            self.store.insert_wallet(address, encrypted, salt)
            del key
            self.address = address
            self.review = None
            return {"address": address, "phrase": phrase}
        if action == "unlock":
            address, key = crypto.unlock_wallet_records(self.store.get_wallet_records_by_address(args["address"]), args["password"])
            del key
            self.address = address
            self.review = None
            return {"address": address}
        if action == "backup":
            wallet_backup.save_backup(self.store.snapshot(), args["password"], args["path"])
            return {"saved": True}
        if action == "restore":
            self.lock()
            count = self.store.restore_snapshot(wallet_backup.load_backup(args["path"], args["password"]))
            return {"added": count}
        if action == "import_database":
            self.lock()
            path = Path(args["path"])
            if path.stat().st_size > wallet_backup.MAX_BACKUP_BYTES:
                raise ValueError("Wallet database exceeds import limit")
            return {"added": self.store.restore_snapshot(path.read_bytes())}
        if action == "history":
            return {"rows": self.store.history(args["address"])}
        if action == "reconcile":
            from .node_history import NodeHistory,matches_payment,verify_anchor
            owner = crypto.validate_address(args["address"])
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
        if not self.address:
            raise ValueError("Unlock a wallet first")
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
            fee = client.minimum_fee()
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
            owner, key = crypto.unlock_wallet_records(self.store.get_wallet_records_by_address(self.address), args["password"])
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


def serve(directory):
    signer = Signer(directory)
    for line in sys.stdin.buffer:
        response = {"id": None}
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
    signer.lock()
    signer.store.con.close()
