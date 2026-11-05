"""Explicit connection policy; signing remains in the local private helper."""
from . import settings

LOCAL_ENDPOINT = 'http://127.0.0.1:19400'
LITE_ENDPOINT = 'https://mainnet.wartnode.com'
EXPLORER_ENDPOINT = 'https://wartnode.com/api/v1/mainnet'


def context(directory):
    value = settings.load(directory)
    return (LITE_ENDPOINT if value['connection_mode']=='lite' else LOCAL_ENDPOINT,
            value['connection_revision'])


def client(directory, **options):
    from .rpc import NodeRPC
    return NodeRPC(context(directory)[0], **options)


def history(client, chain_path):
    if getattr(client,'remote',False):
        from .remote_history import RemoteHistory
        return RemoteHistory(client)
    from .node_history import NodeHistory
    return NodeHistory(client, chain_path)
