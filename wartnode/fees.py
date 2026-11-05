"""Owner-selected wallet fee and managed-node admission minimum, in exact units."""
TRANSACTION_FEE_E8 = 9992
NODE_MINIMUM_FEE = '0.00009992'


def transaction_fee(node_minimum):
    if type(node_minimum) is not int or node_minimum < 0:
        raise ValueError('The node returned an invalid minimum fee')
    if node_minimum > TRANSACTION_FEE_E8:
        raise ValueError('The selected node requires a fee above 0.00009992 WART. '
                         'Use a node that accepts this fee; no payment was signed.')
    return TRANSACTION_FEE_E8
