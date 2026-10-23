"""Measure preview hand-off without motion; run while the menu service is stopped."""
import json
from pathlib import Path
import time

import typer

from ec_26_e.gui.camera_pipe import CameraPipe
from ec_26_e.gui.menu_model import MODES
from ec_26_e.tasks.execution_lock import TaskExecutionLock

app = typer.Typer(pretty_exceptions_show_locals=False)


def wait_for(test, camera, seconds=15):
    deadline = time.monotonic() + seconds
    while not test():
        if camera.error:
            raise RuntimeError(camera.error)
        if time.monotonic() >= deadline:
            raise TimeoutError('Preview benchmark timed out')
        time.sleep(.005)


@app.command()
def main(output: Path = typer.Argument(...), trials: int = typer.Option(5, min=1, max=30)):
    camera = CameraPipe()
    records = []
    try:
        started = time.monotonic()
        camera.prime()
        wait_for(lambda: camera.ready, camera)
        preload = time.monotonic() - started
        pid = camera.process.pid
        for trial in range(trials):
            label = list(MODES)[trial % len(MODES)]
            started = time.monotonic()
            camera.start(label)
            wait_for(lambda: camera.latest is not None, camera)
            first = time.monotonic() - started
            time.sleep(.8)
            started = time.monotonic()
            camera.stop()
            stopped = time.monotonic() - started
            assert camera.process.pid == pid and camera.process.poll() is None
            with TaskExecutionLock():
                pass  # Reusing imports must not retain hardware ownership.
            records.append(dict(trial=trial, mode=label, first_frame_seconds=first,
                                stop_seconds=stopped, worker_pid=pid))
    finally:
        worker = camera.process
        camera.close()
        if worker is not None and worker.returncode != 0:
            raise RuntimeError(f'Preview worker shutdown failed: {worker.returncode}')
    report = dict(software_preload_seconds=preload, trials=records, motion_executed=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    app()
