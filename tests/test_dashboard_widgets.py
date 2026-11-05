"""Native presentation checks for state labels and painting under Qt."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtWidgets import QApplication
from wartnode.dashboard import ConnectionStatus, MinerSample
from wartnode.widgets import StatusRing, HashrateChart
from test_dashboard_models import wire

app = QApplication.instance() or QApplication([])


class NativeDashboardTests(unittest.TestCase):
    def test_ring_accessibility_and_painting_follow_only_the_selected_state(self):
        ring = StatusRing()
        for mode, raw, name in [('local', 'Ready', 'Synced'), ('local', 'Synchronizing', 'Syncing'),
                                ('lite', 'Connected', 'Lite'), ('local', 'Stopped', 'Offline')]:
            ring.set_status(ConnectionStatus.from_runtime({'state': raw}, mode))
            self.assertEqual(ring.accessibleName(), 'Connection: '+name)
            self.assertFalse(ring.grab().isNull())
        ring.close()

    def test_chart_renders_empty_flat_and_changed_ranges(self):
        chart = HashrateChart()
        chart.resize(320, 160)
        self.assertFalse(chart.grab().isNull())
        for rates in [(0, 0), (10, 10), (10, 400000000)]:
            chart.set_samples([MinerSample.from_wire(wire(elapsed_s=i+1, janus_hps=rate))
                               for i, rate in enumerate(rates)])
            self.assertFalse(chart.grab().isNull())
        chart.close()


if __name__ == '__main__':
    unittest.main()
