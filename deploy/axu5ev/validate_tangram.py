"""七巧板板端实拍/菜单验收；所有入口强制只规划，不构造运动执行器。"""
from dataclasses import replace
import json
import os
from pathlib import Path
import time

import typer

app=typer.Typer(pretty_exceptions_show_locals=False)


def validate_gui(output):
    import tkinter as tk
    from ec_26_e.gui.board_menu import BoardMenu
    from validate_menu_runtime import framebuffer
    path=output/'plan-only-menu.json'
    profile=json.loads(Path('/opt/ec26e/config/axu5ev/menu.json').read_text())
    profile.update(plan_only=True,motion_enabled=False)
    path.write_text(json.dumps(profile))
    os.environ.update(EC_MENU_CONFIG=str(path),EC_MOTION_ENABLED='false')
    class TestMenu(BoardMenu):
        def write_status(self):pass
    root=tk.Tk();menu=TestMenu(root);root.lift()
    menu.display_directory=output/'gui-display'
    result={};started=time.monotonic()
    def finish():
        (output/'gui-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        menu.close()
    def preview():
        if not menu.camera.ready or (menu.warm_task is not None and not menu.warm_task.ready):
            if time.monotonic()-started>20:
                result['error']='菜单预热超时';finish();return
            root.after(100,preview);return
        menu.choose(2)
        root.update_idletasks()
        framebuffer(output/'gui-menu.png')
        root.after(100,solve)
    def solve():
        menu.action('confirm','tangram-validation-plan-only')
        root.after(500,poll)
    def poll():
        if menu.model.busy or menu.transition is not None:
            if time.monotonic()-started>100:
                menu.action('cancel','validation-timeout')
            root.after(500,poll);return
        menu.poll_prediction()
        root.update_idletasks()
        framebuffer(output/'gui-prediction.png')
        result.update(prediction_state=menu.display_state,message=menu.model.message,
            motion_executed=False,elapsed_seconds=time.monotonic()-started)
        finish()
    root.after(100,preview);root.mainloop()
    if result.get('prediction_state')!='ready':raise RuntimeError(f'菜单只规划失败：{result}')


@app.command()
def main(output: Path=typer.Option(...,'--output'), gui: bool=typer.Option(False,'--gui')):
    from puzzle_solver import PATTERN_NAMES,PuzzlePiece,plan_tangram_moves
    from ec_26_e.config.tangram import load_tangram_config
    from ec_26_e.tasks.tangram import run_tangram,validate_tangram_machine_plan
    from ec_26_e.tasks.execution_lock import TaskExecutionLock
    state=json.loads(Path('/run/ec26e/menu-status.json').read_text())
    if state.get('busy') or state.get('preview'):raise RuntimeError('生产菜单忙碌')
    output.mkdir(parents=True,exist_ok=True)
    os.environ['EC_MOTION_ENABLED']='false'
    config=load_tangram_config(Path('/opt/ec26e/config/axu5ev/tasks/tangram.json'),
        env_path=Path('/opt/ec26e/config/axu5ev/machine.env'))
    config=replace(config,stable_frames=1)
    records=[]
    pieces=None
    for name in PATTERN_NAMES:
        os.environ.update(EC_TASK_DISPLAY_DIR=str(output/'display'),EC_TASK_DISPLAY_ID=name)
        start=time.monotonic()
        def forbidden(_):raise AssertionError('验证期间禁止构造运动执行器')
        selected=replace(config,planning=replace(config.planning,pattern_id=name))
        if pieces is None:
            with TaskExecutionLock():
                plan=run_tangram(selected,execute_motion=False,actuator_factory=forbidden)
            pieces=tuple(PuzzlePiece(m.piece_id,m.observed_vertices_a4_mm) for m in plan.moves)
        else:
            plan=plan_tangram_moves(pieces,selected.planning)
            validate_tangram_machine_plan(plan,selected)
        records.append(dict(pattern=name,elapsed_seconds=time.monotonic()-start,
            acquisition_mode='single_shared_snapshot',temporal_stability_validated=False,
            motion_executed=False,plan=plan.to_dict()))
        print(name,'complete',round(records[-1]['elapsed_seconds'],3),flush=True)
        (output/'live-results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
    os.environ.pop('EC_TASK_DISPLAY_DIR',None);os.environ.pop('EC_TASK_DISPLAY_ID',None)
    if gui:validate_gui(output)


if __name__=='__main__':app()
