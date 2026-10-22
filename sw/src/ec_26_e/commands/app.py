"""无电机副作用的迁移验证入口；真实运动继续使用 ec-26-e。"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import platform
import time
from pathlib import Path
import cv2
import numpy as np
import typer
from ec_26_e.control import PuzzlePiece, PlanningOptions, plan_sheet_moves
from ec_26_e.vision import Resize, ResizeConfig, Detect1Config
from ec_26_e.vision.factory import create_production_detector
from ec_26_e.tasks.task1.review import _review_relative_vertices

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)
app = typer.Typer(no_args_is_help=True, add_completion=False, help="7020 基线与离线验证；不发送电机命令。")

def write_json(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")

@app.command()
def solve(
    observations: Path = typer.Argument(..., exists=True, dir_okay=False),
    output: Path = typer.Option(Path("outputs/solve.json"), "--output"),
):
    """对毫米顶点 JSON 求解，保存性能、计划与失败原因。"""
    payload=json.loads(observations.read_text(encoding="utf-8"))
    pieces=tuple(PuzzlePiece(str(p['id']),tuple(tuple(float(v) for v in xy) for xy in p['vertices_mm'])) for p in payload['pieces'])
    options=PlanningOptions(mode=payload['mode'],noise_bound_mm=payload.get('noise_bound_mm',0.0))
    started=time.perf_counter()
    report={'schema_version':1,'input_sha256':hashlib.sha256(observations.read_bytes()).hexdigest(),'platform':platform.platform(),'motion_executed':False}
    try:
        report.update(success=True,plan=plan_sheet_moves(pieces,options).to_dict())
    except (ValueError,RuntimeError) as error:
        report.update(success=False,error=str(error))
    report['solve_ms']=(time.perf_counter()-started)*1000
    write_json(output,report)
    if not report['success']: raise typer.Exit(1)

@app.command()
def evaluate(
    image: Path = typer.Argument(..., exists=True, dir_okay=False),
    mode: str = typer.Option("fixed", "--mode"),
    pieces: int = typer.Option(4, "--pieces", min=2,max=4),
    corrected: bool = typer.Option(False,"--corrected",help="输入已校正到 292:207 平面"),
    output: Path = typer.Option(Path("outputs/evaluation"),"--output"),
    backend: str = typer.Option("cpu","--backend",envvar="EC_PREPROCESS_BACKEND"),
):
    """图像读取、透视校正、轮廓、求解；重复静态图不算真实多帧验收。"""
    if mode not in {'fixed','random'} or backend not in {'cpu','pl'}:
        raise typer.BadParameter("mode=fixed/random，backend=cpu/pl")
    if mode=='fixed' and pieces!=4: raise typer.BadParameter("fixed 必须四片")
    output.mkdir(parents=True,exist_ok=True)
    start=time.perf_counter()
    raw=cv2.imread(str(image))
    if raw is None: raise typer.BadParameter("无法读取图像")
    loaded=time.perf_counter()
    report={'schema_version':1,'platform':platform.platform(),'input_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
        'motion_executed':False,'temporal_stability_validated':False,'timings_ms':{'read_decode':(loaded-start)*1000}}
    try:
        corrected_image=raw if corrected else Resize(ResizeConfig()).resize(raw).image_bgr
        resized=time.perf_counter()
        report['timings_ms']['resize']=(resized-loaded)*1000
        with create_production_detector(Detect1Config(short_edge_merge_mm=8,minimum_physical_edge_mm=8,
            expected_pieces=pieces,preprocess_backend=backend,pl_bitstream=os.environ.get('EC_PL_BITSTREAM'))) as stable:
            detector=stable.detector
            result=detector.detect(corrected_image)
            report['detection']=result.to_dict()
            report['diagnostics']=detector.last_diagnostics.to_dict()
            report['timings_ms']['vision']=(time.perf_counter()-resized)*1000
            if len(result.pieces)!=pieces: raise ValueError(detector.last_failure or "纸片数不正确")
            observations=tuple(_review_relative_vertices(p.group_id,p.corners_relative,'counterclockwise') for p in result.pieces)
            write_json(output/'observations.json',{'mode':mode,'noise_bound_mm':1.0,'pieces':[{'id':p.piece_id,'vertices_mm':p.vertices_mm} for p in observations]})
            canvas=corrected_image.copy()
            for piece in result.pieces:
                cv2.polylines(canvas,[np.rint(piece.corners_px).astype(np.int32)],True,(0,0,255),1)
            cv2.imwrite(str(output/'contours.png'),canvas)
            planning=time.perf_counter()
            try:
                report['plan']=plan_sheet_moves(observations,PlanningOptions(mode=mode,noise_bound_mm=1.0)).to_dict()
            finally:
                report['timings_ms']['solve']=(time.perf_counter()-planning)*1000
        report['success']=True
    except (ValueError,RuntimeError,OSError) as error:
        report.update(success=False,error=str(error))
    report['timings_ms']['total']=(time.perf_counter()-start)*1000
    write_json(output/'report.json',report)
    if not report['success']: raise typer.Exit(1)

@app.command('compare-pl')
def compare_pl(
    image: Path = typer.Argument(...,exists=True,dir_okay=False),
    bitstream: Path = typer.Option(...,'--bitstream',exists=True,envvar='EC_PL_BITSTREAM'),
    frames: int = typer.Option(100,'--frames',min=2),
    output: Path = typer.Option(Path('outputs/pl-comparison.json'),'--output'),
):
    """板上重复整帧 DMA 验证；报告含分配、打包、传输的端到端耗时。"""
    from ec_26_e.vision.preprocess import CpuPreprocessor
    from ec_26_e.vision.pl import PynqPreprocessor
    source=cv2.imread(str(image))
    if source is None: raise typer.BadParameter('无法读取图像')
    expected=CpuPreprocessor().process(source,1)
    backend=PynqPreprocessor(str(bitstream))
    timings=[]
    try:
        for frame_id in range(1,frames+1):
            started=time.perf_counter()
            actual=backend.process(source,frame_id)
            timings.append((time.perf_counter()-started)*1000)
            if not np.array_equal(expected,actual):
                raise RuntimeError(f'第 {frame_id} 帧掩膜不一致，差异像素 {np.count_nonzero(expected!=actual)}')
    finally:
        backend.close()
    write_json(output,{'frames':frames,'exact_match':True,'end_to_end_ms':timings,'mean_ms':float(np.mean(timings))})

@app.command()
def summarize(
    reports: Path = typer.Argument(...,exists=True,file_okay=False),
    output: Path = typer.Option(Path('outputs/acceptance-summary.json'),'--output'),
):
    """汇总人工整机验收 JSON；未做机械测量时不生成假误差。"""
    rows=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(reports.glob('*.json'))]
    summary={}
    for mode in ('fixed','random'):
        selected=[r for r in rows if r.get('mode')==mode and r.get('kind')=='hardware_acceptance' and type(r.get('success')) is bool]
        errors=[float(r['max_error_mm']) for r in selected if r.get('max_error_mm') is not None]
        summary[mode]={'trials':len(selected),'successes':sum(r.get('success') is True for r in selected),
            'minimum_20_trials':len(selected)>=20,'max_error_mm':max(errors) if errors else None}
    write_json(output,summary)

@app.command()
def record(
    output: Path = typer.Option(Path('outputs/capture'),'--output'),
    frames: int = typer.Option(30,'--frames',min=1),
    device: Path = typer.Option(Path('/dev/video0'),'--device',envvar='CAMERA_DEVICE_PATH'),
    width: int = typer.Option(1280,'--width',min=1),
    height: int = typer.Option(720,'--height',min=1),
    input_format: str = typer.Option('mjpeg','--input-format'),
):
    """板端采集原图及到达时间；不启动运动，耗时包括采集和解码。"""
    from ec_26_e.cam import CameraConfig,FFmpegCamera
    output.mkdir(parents=True,exist_ok=True)
    records=[];sequence=None;start=time.perf_counter()
    with FFmpegCamera(CameraConfig(device_path=device,width=width,height=height,input_format=input_format)) as camera:
        for index in range(frames):
            waiting=time.perf_counter()
            frame=camera.read_latest(after_sequence=sequence)
            sequence=frame.sequence_id
            ready=time.perf_counter()
            path=output/f'{index:04d}.png'
            if not cv2.imwrite(str(path),frame.image_bgr):raise RuntimeError('原图保存失败')
            records.append({'file':path.name,'sequence_id':sequence,'received_at_ns':frame.received_at_ns,'wait_for_decoded_frame_ms':(ready-waiting)*1000})
    write_json(output/'capture.json',{'motion_executed':False,'total_ms':(time.perf_counter()-start)*1000,'frames':records})

@app.command('plan-live')
def plan_live(
    env: Path = typer.Option(..., '--env', exists=True, dir_okay=False, envvar='EC_GUI_ENV'),
    config_dir: Path = typer.Option(..., '--config-dir', exists=True, file_okay=False),
    mode: str = typer.Option('fixed', '--mode'),
    backend: str = typer.Option('cpu', '--backend', envvar='EC_PREPROCESS_BACKEND'),
    output: Path = typer.Option(Path('outputs/live-plan.json'), '--output'),
):
    """真实相机稳定识别并保存计划；不打开电机串口或电磁铁 GPIO。"""
    if mode not in ('fixed', 'random') or backend not in ('cpu', 'pl'):
        raise typer.BadParameter('mode=fixed/random，backend=cpu/pl')
    from ec_26_e.gui.live_plan import run_live_plan
    try:
        run_live_plan(env, config_dir, mode, backend, output)
    except KeyboardInterrupt:
        raise typer.Exit(130)
    except Exception as error:
        logger.error('只求解任务失败：%s', error)
        raise typer.Exit(1)
    typer.echo(f'求解完成，未执行运动：{output}')


@app.command()
def gui(
    env: Path = typer.Option(Path('.env'),'--env',exists=True,envvar='EC_GUI_ENV'),
    config_dir: Path = typer.Option(Path('configs/zynq7020'),'--config-dir',exists=True,file_okay=False),
    backend: str = typer.Option('pl','--backend',envvar='EC_PREPROCESS_BACKEND'),
    fullscreen: bool = typer.Option(True,'--fullscreen/--windowed'),
    demo: Path | None = typer.Option(None,'--demo',exists=True,dir_okay=False,help='已校正的静态图像演示；禁用真实任务'),
    motion_enabled: bool = typer.Option(False,'--motion-enabled/--preview-only',envvar='EC_MOTION_ENABLED',help='接线和标定完成、已加载运动版 bitstream 后启用真实任务'),
):
    """HDMI 操作台：模式选择、实时轮廓和单次真实拼图。默认 PL 后端。"""
    if backend not in {'cpu','pl'}:
        raise typer.BadParameter('backend 必须为 cpu 或 pl')
    from ec_26_e.gui.controller import Controller
    try:
        from ec_26_e.gui.window import launch
    except ImportError as error:
        raise typer.BadParameter('界面需要 Python Tk 支持；Linux 请安装 python3-tk') from error
    launch(Controller(env,config_dir,backend,demo,motion_enabled=motion_enabled),fullscreen)

if __name__=='__main__': app()
