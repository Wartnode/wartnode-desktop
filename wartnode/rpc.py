"""Bounded, redirect-free RPC to the local node or the fixed HTTPS Lite service."""
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .network import open_response

HEX64 = re.compile(r"[a-fA-F0-9]{64}\Z")
GENESIS = hashlib.sha256(b"The New York Times International Edition 29/06/2023: 'Water troubles: A preview'").hexdigest()
ZERO_ADDRESS = "0000000000000000000000000000000000000000de47c9b2"


class NodeError(ValueError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class BroadcastUncertain(NodeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise NodeError("Node redirects are forbidden")


class NodeRPC:
    expected_network = "mainnet"

    def __init__(self, peer="http://127.0.0.1:19400", cancel=None, deadline=None, network="mainnet"):
        parsed = urlsplit(peer)
        from .connection import LITE_ENDPOINT
        self.remote = peer == LITE_ENDPOINT
        if network != 'mainnet' or (not self.remote and (
                parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or not parsed.port or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password)):
            raise NodeError("Choose the managed local node or the configured HTTPS Lite service")
        self.peer, self.cancel = peer, cancel
        self.deadline = deadline or time.monotonic() + 8
        handlers=[urllib.request.ProxyHandler({}),NoRedirect()]
        if self.remote:
            from .tls import client_context
            handlers.append(urllib.request.HTTPSHandler(context=client_context()))
        self.opener = urllib.request.build_opener(*handlers)

    def check_cancelled(self):
        if self.cancel is not None and self.cancel.is_set():
            raise NodeError("Operation cancelled")
        if time.monotonic() >= self.deadline:
            raise NodeError("Node operation timed out")

    def request(self, path, payload=None):
        self.check_cancelled()
        if not path.startswith("/") or ".." in path or any(c in path for c in "?#\\"):
            raise NodeError("Invalid node request path")
        if self.remote:
            read_route=(path in ('/chain/head','/transaction/minfee','/chain/hashrate/100') or re.fullmatch(
                r'/chain/block/(?:[1-9][0-9]{0,9}|[a-fA-F0-9]{64})(?:/header)?|/account/[a-fA-F0-9]{48}/(?:balance|history/[0-9]{1,19})',path))
            if not (read_route and payload is None or path=='/transaction/add' and isinstance(payload,dict)):
                raise NodeError('This operation is unavailable in Lite mode')
        request = urllib.request.Request(self.peer + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with open_response(self.opener, request, self.deadline, self.cancel) as response:
                if response.status != 200:
                    raise NodeError("Node returned an unexpected HTTP status")
                raw = response.read_bounded(4 * 1024 * 1024)
                result = json.loads(raw)
        except (OSError, urllib.error.URLError, ValueError) as exc:
            if payload is not None:
                raise BroadcastUncertain("Broadcast result is uncertain. Check the saved payment before sending again.") from exc
            raise NodeError("The node is unavailable or returned an invalid response") from exc
        if isinstance(result, list) and path == "/peers/connected/connection":
            return result
        if not isinstance(result, dict) or type(result.get("code")) is not int:
            if payload is not None:
                raise BroadcastUncertain("Unrecognized broadcast reply; payment remains reserved")
            raise NodeError("Unsupported node reply")
        if result["code"] != 0:
            raise NodeError(str(result.get("error", "Node rejected request"))[:200], result["code"])
        data = result.get("data")
        if not isinstance(data, (dict, list)):
            if payload is not None:
                raise BroadcastUncertain("Incomplete broadcast reply; payment remains reserved")
            raise NodeError("Node reply has no data")
        return data

    def head(self, require_synced=False):
        head = self.request("/chain/head")
        if (not isinstance(head, dict) or "chainHead" in head or type(head.get("height")) is not int or not 0<=head['height']<=0xffffffff
                or not HEX64.fullmatch(str(head.get("hash", "")))
                or type(head.get("synced")) is not bool):
            raise NodeError("Unsupported chain head")
        if head["height"] == 0:
            if head["hash"].lower() != GENESIS:
                raise NodeError("Unexpected chain genesis")
            head["synced"] = False
        if require_synced and not head["synced"]:
            raise NodeError("Wait for the node to finish syncing")
        return head

    def template(self, address=ZERO_ADDRESS):
        if self.remote:raise NodeError('Mining requires the managed local node')
        result = self.request("/chain/mine/" + address)
        if result.get("testnet") is not False:
            raise NodeError("The node did not confirm that it is on Warthog mainnet")
        return result

    def require_network(self):
        if self.remote:
            from .node_history import MAINNET_BLOCK_ONE, verify_anchor
            head = self.head(require_synced=True)
            first = self.request('/chain/block/1/header').get('header',{})
            if first.get('hash') != MAINNET_BLOCK_ONE:
                raise NodeError('The service returned an unexpected chain anchor')
            pin = head.get('pinHeight')
            if type(pin) is not int or not 0 < pin <= head['height'] or not HEX64.fullmatch(str(head.get('pinHash',''))):
                raise NodeError('The service returned an invalid signing context')
            verify_anchor(self, {'height':pin,'hash':head['pinHash']})
            verify_anchor(self, {'height':head['height'],'hash':head['hash']})
            return
        if not self.connected_peers():
            raise NodeError('The local node has no connected peers')
        head = self.head(require_synced=True)
        template = self.template()
        header = template.get('header','')
        if (template.get('synced') is not True or template.get('height') != head['height']+1
                or not isinstance(header,str) or len(header)!=160 or header[:64].lower()!=head['hash'].lower()):
            raise NodeError('The node is not ready or the blockchain changed. Wait for sync to finish and try again.')

    def connected_peers(self):
        peers = self.request('/peers/connected/connection')
        # The pinned mainnet API wraps the endpoint's list in another data key.
        if isinstance(peers, dict):
            peers = peers.get('data')
        if not isinstance(peers, list) or any(not isinstance(peer, str) for peer in peers):
            raise NodeError('Unsupported connected-peer response')
        return peers

    def balance(self, address):
        from .compat import wallet_utils as crypto
        value = self.request("/account/" + crypto.validate_address(address) + "/balance").get("balance")
        crypto.parse_e8(value, allow_zero=True)
        return value

    def minimum_fee(self):
        from .compat import wallet_utils as crypto
        value = self.request("/transaction/minfee").get("E8")
        if crypto.round_fee_e8(value) != value:
            raise NodeError("Node returned an invalid fee")
        return value

    def broadcast(self, payload, expected_hash=None):
        result = self.request("/transaction/add", payload)
        value = result.get("txHash")
        if not HEX64.fullmatch(str(value)) or (expected_hash and value.lower() != expected_hash):
            raise BroadcastUncertain("Node did not acknowledge the saved payment hash")
        return value.lower()

    def block(self, height):
        if isinstance(height,str) and HEX64.fullmatch(height):
            return self.request('/chain/block/'+height.lower())
        if type(height) is not int or height < 1:
            raise NodeError("Invalid block height")
        return self.request(f"/chain/block/{height}")
