"""The selected fee is exact, compact-representable, and never silently raised."""
import unittest
from wartnode.fees import transaction_fee, TRANSACTION_FEE_E8, NODE_MINIMUM_FEE
from wartnode.compat import wallet_utils as crypto


class SelectedFeeTests(unittest.TestCase):
    def test_wallet_and_node_share_one_exact_representable_amount(self):
        self.assertEqual(crypto.parse_e8(NODE_MINIMUM_FEE),TRANSACTION_FEE_E8)
        self.assertEqual(TRANSACTION_FEE_E8,9992)
        self.assertEqual(crypto.round_fee_e8(TRANSACTION_FEE_E8),TRANSACTION_FEE_E8)
        for minimum in (0,1000,9992):
            self.assertEqual(transaction_fee(minimum),9992)

    def test_a_higher_backend_minimum_blocks_instead_of_changing_the_reviewed_fee(self):
        for minimum in (9993,1000448):
            with self.assertRaisesRegex(ValueError,'above 0.00009992'):
                transaction_fee(minimum)
        for invalid in (-1,True,'9992',None):
            with self.assertRaises(ValueError):transaction_fee(invalid)


if __name__ == '__main__':unittest.main()
