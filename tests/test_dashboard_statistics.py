"""Statistics use verified node fields and measured process counters."""
import os
import unittest
from wartnode.statistics import network_statistics, ProcessSampler, ProcessCounters, windows_process_counters, difficulty_text
from wartnode.rpc import NodeError


class StatsRPC:
    peer='http://127.0.0.1:19400'
    def require_network(self):pass
    def head(self,require_synced=False):return dict(height=101,hash='ab'*32,synced=True,difficulty=1234)
    def block(self,height):return dict(header=dict(hash='ab'*32,timestamp=2000),body=dict(rewards=[dict(amount='1.50000000')]))
    def request(self,path):
        if path=='/chain/hashrate/100':return dict(lastNBlocksEstimate=3000000000,N=100)
        return dict(header=dict(timestamp=500))
    def minimum_fee(self):return 9992


class StatisticsTests(unittest.TestCase):
    def test_difficulty_uses_gh_th_magnitudes_without_a_rate_suffix(self):
        self.assertEqual(difficulty_text(1704000000000),'1.70 TH')
        self.assertEqual(difficulty_text(86200000000),'86.20 GH')
        self.assertEqual(difficulty_text(1000000000000),'1.00 TH')
        self.assertEqual(difficulty_text(None),'—')
        self.assertEqual(difficulty_text(float('nan')),'—')

    def test_network_values_are_derived_from_actual_fields_and_window(self):
        value=network_statistics(StatsRPC())
        self.assertEqual(value['hashrate'],3000000000)
        self.assertEqual(value['interval'],15)
        self.assertEqual(value['interval_blocks'],100)
        self.assertEqual(value['reward'],150000000)
        self.assertEqual(value['minimum_fee'],9992)

    def test_changed_head_cannot_publish_a_mixed_network_snapshot(self):
        class Changed(StatsRPC):
            def block(self,height):return dict(header=dict(hash='cd'*32),body=dict(rewards=[]))
        with self.assertRaises(NodeError):network_statistics(Changed())

    def test_process_cpu_is_normalized_to_total_capacity_and_resets_on_pid_reuse(self):
        readings=iter([ProcessCounters(1,100,300,400),ProcessCounters(1,10000100,310,410),
                       ProcessCounters(2,1,320,420),None])
        times=iter([1,2,3,4])
        sampler=ProcessSampler(reader=lambda _:next(readings),clock=lambda:next(times),cores=2)
        self.assertIsNone(sampler.sample(100)['cpu_percent'])
        self.assertEqual(sampler.sample(100)['cpu_percent'],50)
        self.assertIsNone(sampler.sample(100)['cpu_percent'])
        missing=sampler.sample(100)
        self.assertIsNone(missing['memory_bytes'])
        self.assertIsNone(missing['cpu_percent'])

    @unittest.skipUnless(os.name=='nt','Windows process query')
    def test_windows_counter_adapter_reads_its_own_process_without_extra_privileges(self):
        value=windows_process_counters(os.getpid())
        self.assertIsNotNone(value)
        self.assertGreater(value.created,0)
        self.assertGreater(value.working_set,0)
        self.assertGreaterEqual(value.cpu_ticks,0)


if __name__ == '__main__':unittest.main()
