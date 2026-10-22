"""原子选择下次启动的候选核硬件包；不重编程运行中的 PL。"""
import hashlib
import importlib.util
import json
from pathlib import Path

import typer
from ec_26_e.tasks.execution_lock import TaskExecutionLock

BASE = Path('/opt/ec26e')
app = typer.Typer(pretty_exceptions_show_locals=False)


def replace(path, data):
    temporary = path.with_suffix('.candidate.tmp')
    temporary.write_bytes(data)
    if path.exists():
        temporary.chmod(path.stat().st_mode & 0o777)
    temporary.replace(path)


@app.command()
def main(release: Path = typer.Option(...), apply: bool = typer.Option(False),
         rollback: bool = typer.Option(False)):
    release = release.resolve()
    if not release.is_relative_to(BASE/'releases'):
        raise ValueError('使用 /opt/ec26e/releases 下的发布目录')
    config = BASE/'config/axu5ev/hardware.json'
    supervisor = BASE/'install/autostart.py'
    backup = release/'hardware-selection-before'
    bundle = release/'hardware'
    if not rollback:
        spec = importlib.util.spec_from_file_location('candidate_loader', bundle/'load_hardware.py')
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
        loader.verify_bundle(bundle)
        loader.check_base()
        if 'puzzle_candidate_scan_0' not in (bundle/'puzzle5ev.hwh').read_text():
            raise ValueError('新包未包含候选扫描核')
        # Candidate software must be installed before selecting its hardware.
        from ec_26_e.control.pl_candidate import ABI
        from puzzle_solver.candidate_acceleration import CandidateScanBackend
        assert ABI == 0x434e0101
    if not apply:
        print(json.dumps(dict(bundle=str(bundle), reboot_required=True,
            motion_executed=False, rollback=rollback, applied=False)))
        return
    with TaskExecutionLock():
        menu = json.loads(Path('/run/ec26e/menu-status.json').read_text())
        if menu.get('busy') or menu.get('preview') or menu.get('transition'):
            raise RuntimeError('菜单忙，未切换下次启动配置')
        if rollback:
            saved = json.loads((backup/'record.json').read_text())
            if hashlib.sha256(config.read_bytes()).hexdigest() != saved['selected_sha256']:
                raise RuntimeError('硬件选择已被后续改动，不能覆盖回滚')
            if hashlib.sha256(supervisor.read_bytes()).hexdigest() != saved['supervisor_sha256']:
                raise RuntimeError('启动监督程序已被后续改动，不能覆盖回滚')
            replace(supervisor, (backup/'autostart.py').read_bytes())
            if (backup/'hardware.json').exists():
                replace(config, (backup/'hardware.json').read_bytes())
            else:
                config.unlink()
        else:
            backup.mkdir()  # Refuse to overwrite the previous choice/rollback data.
            previous = supervisor.read_bytes()
            (backup/'autostart.py').write_bytes(previous)
            if config.exists():
                (backup/'hardware.json').write_bytes(config.read_bytes())
            selected = (json.dumps(dict(bundle=str(bundle), candidate_backend='pl'), indent=2)+'\n').encode()
            selected_supervisor = (release/'autostart.py').read_bytes()
            try:
                replace(supervisor, selected_supervisor)
                replace(config, selected)
                (backup/'record.json').write_text(json.dumps(dict(
                    selected_sha256=hashlib.sha256(selected).hexdigest(), reboot_required=True,
                    supervisor_sha256=hashlib.sha256(selected_supervisor).hexdigest(),
                    bit_sha256=hashlib.sha256((bundle/'puzzle5ev.bit').read_bytes()).hexdigest())))
            except BaseException:
                replace(supervisor, previous)
                if (backup/'hardware.json').exists():
                    replace(config, (backup/'hardware.json').read_bytes())
                else:
                    config.unlink(missing_ok=True)
                raise
        print('Next-boot selection updated. Running PL unchanged; cold restart required. No motion.')


if __name__ == '__main__':
    app()
