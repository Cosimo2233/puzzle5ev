"""Validate real menu worker handoff with a geometry-only command, never motion."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

from ec_26_e.gui.warm_task import WarmTaskSlot
from ec_26_e.tasks.execution_lock import TaskExecutionLock


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.output.with_suffix('.input.json')
    source.write_text(json.dumps(json.loads((args.root/'corpus.json').read_text())[0]))
    plan = args.output.with_suffix('.plan.json')
    slot = WarmTaskSlot()
    with TaskExecutionLock():
        try:
            slot.prime()
            deadline = time.monotonic()+30
            while not slot.ready:
                if slot.process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Module preloading failed')
                time.sleep(.01)
            idle_pid = slot.process.pid
            descriptors = []
            for fd in Path(f'/proc/{idle_pid}/fd').iterdir():
                try:
                    descriptors.append(str(fd.readlink()))
                except FileNotFoundError:
                    pass
            forbidden = ('/dev/uio', '/dev/video', '/dev/ttyUSB', '/dev/ttyACM',
                         '/dev/ec26e-', '/dev/gpiochip', '/dev/mem')
            assert not any(p.startswith(forbidden) for p in descriptors), descriptors
            environment = dict(os.environ, EC_MOTION_ENABLED='false', EC_RIGID_BACKEND='pl',
                               EC_CLEARANCE_BACKEND='pl', EC_DRAW_ENABLED='false')
            started = time.monotonic()
            process = slot.dispatch([sys.executable, '-u', '-m', 'ec_26_e.commands.app',
                                     'solve', str(source), '--output', str(plan)], environment)
            assert process.pid == idle_pid
            slot.close()  # Must not terminate the dispatched task.
            try:
                status = process.wait(timeout=30)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            report = json.loads(plan.read_text())
            assert status == 0 and report['success'] and report['motion_executed'] is False
            args.output.write_text(json.dumps(dict(passed=True, motion_executed=False,
                idle_hardware_open=False, reused_pid=idle_pid, seconds=time.monotonic()-started,
                idle_descriptors=descriptors), indent=2))
        finally:
            slot.close()


if __name__ == '__main__':
    main()
