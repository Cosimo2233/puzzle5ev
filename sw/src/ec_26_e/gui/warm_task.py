"""Preload software only; execute exactly one explicit menu request, then exit."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def prepare():
    # Importing these modules constructs no camera, PL backend or actuator.
    from ec_26_e.command.app import app as motion_app
    from ec_26_e.commands.app import app as plan_app
    from ec_26_e.gui.controller import load_profile
    import cv2
    cv2.setNumThreads(1)
    return {'ec_26_e': motion_app, 'ec_26_e.commands.app': plan_app}


def execute_request(request, applications):
    module = request['module']
    if module not in applications or not isinstance(request['args'], list):
        raise ValueError('Invalid task entry point')
    environment = request['environment']
    if not isinstance(environment, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                  for k, v in environment.items()):
        raise ValueError('Invalid task environment')
    os.environ.update(environment)
    applications[module](args=request['args'], prog_name=module)


class WarmTaskSlot:
    """Own only an idle worker. Once dispatched, MenuModel owns its process."""
    def __init__(self):
        self.process = None
        self._ready = None

    def prime(self):
        if self.process is not None and self.process.poll() is None:
            return
        self.close()
        fd, name = tempfile.mkstemp(prefix='ec26e-warm-', suffix='.ready')
        os.close(fd)
        self._ready = Path(name)
        self.process = subprocess.Popen([sys.executable, '-u', '-m', __name__, name],
                                        stdin=subprocess.PIPE, text=True)

    @property
    def ready(self):
        try:
            return (self.process is not None and self.process.poll() is None
                    and self._ready is not None and self._ready.stat().st_size > 0)
        except FileNotFoundError:
            return False

    def dispatch(self, command, environment):
        if len(command) < 4 or command[:3] != [sys.executable, '-u', '-m']:
            raise ValueError('Warm worker requires a Python module command')
        if command[3] not in ('ec_26_e', 'ec_26_e.commands.app'):
            raise ValueError('Unsupported warm task module')
        self.prime()
        process = self.process
        process.stdin.write(json.dumps(dict(module=command[3], args=command[4:], environment=environment))+'\n')
        process.stdin.flush()
        process.stdin.close()
        self.process = None
        # The ready file is also removed by the child, including early dispatch.
        if self._ready is not None:
            self._ready.unlink(missing_ok=True)
            self._ready = None
        return process

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()  # This slot never owns a dispatched task.
                self.process.wait(timeout=10)
            if self.process.stdin is not None and not self.process.stdin.closed:
                self.process.stdin.close()
            self.process = None
        if self._ready is not None:
            self._ready.unlink(missing_ok=True)
            self._ready = None


def main():
    ready = Path(sys.argv[1])
    try:
        applications = prepare()
        ready.write_text('ready\n')
        line = sys.stdin.readline()
        if line:
            execute_request(json.loads(line), applications)
    finally:
        ready.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
