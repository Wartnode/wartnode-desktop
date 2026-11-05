"""Consume actual native JSON, rather than a Python copy of its schema."""
from pathlib import Path
import subprocess
import unittest
from wartnode.dashboard import MinerSample
from wartnode.processes import hidden_options

NATIVE=Path(__file__).resolve().parents[1]/'work/ci/miner-build/miner-telemetry-tests.exe'


class NativeTelemetryContract(unittest.TestCase):
    @unittest.skipUnless(NATIVE.is_file(),'Build native miner regressions first')
    def test_emitted_native_fixture_is_accepted_without_sensor_fabrication(self):
        output=subprocess.check_output([str(NATIVE)],text=True,timeout=10,**hidden_options())
        sample=MinerSample.from_wire(output.strip())
        self.assertEqual(sample.session,'fixture-session')
        self.assertEqual(sample.janus_hps,374410000)
        self.assertEqual(sample.accepted,42)
        self.assertEqual(sample.gpus[0].identity,'GPU-selected')
        self.assertEqual(sample.gpus[0].temperature_c,61)
        self.assertIsNone(sample.gpus[0].power_w)


if __name__=='__main__':unittest.main()
