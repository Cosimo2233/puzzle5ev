"""板端候选菜单的预览/只求解验收；测试配置强制禁用运动。"""
import ctypes as C
import json
import os
from pathlib import Path
import time

import typer

app = typer.Typer(pretty_exceptions_show_locals=False)


def framebuffer(path):
    import cv2
    import numpy as np
    # Verified AXU5EV HDMI framebuffer: 1280x720, BGRA32, 5120-byte pitch.
    base = Path('/sys/class/graphics/fb0')
    if (base/'name').read_text().strip() != 'ec26e-hdmi':
        raise RuntimeError('截图必须使用 HDMI framebuffer')
    if (base/'virtual_size').read_text().strip() != '1280,720' or int((base/'bits_per_pixel').read_text()) != 32:
        raise RuntimeError('未知 framebuffer 格式')
    data = Path('/dev/fb0').read_bytes()[:1280*720*4]
    cv2.imwrite(str(path), np.frombuffer(data, np.uint8).reshape(720,1280,4)[:,:,:3])


def cursor_info():
    class Cursor(C.Structure):
        _fields_ = [('x',C.c_short),('y',C.c_short),('width',C.c_ushort),('height',C.c_ushort),
            ('xhot',C.c_ushort),('yhot',C.c_ushort),('serial',C.c_ulong),('pixels',C.POINTER(C.c_ulong)),
            ('atom',C.c_ulong),('name',C.c_char_p)]
    x = C.CDLL('libX11.so.6')
    x.XOpenDisplay.argtypes=[C.c_char_p]; x.XOpenDisplay.restype=C.c_void_p
    display=x.XOpenDisplay(None)
    if not display: raise RuntimeError('Xorg display unavailable')
    fixes=C.CDLL('libXfixes.so.3')
    fixes.XFixesGetCursorImage.argtypes=[C.c_void_p];fixes.XFixesGetCursorImage.restype=C.POINTER(Cursor)
    x.XFree.argtypes=[C.c_void_p];x.XCloseDisplay.argtypes=[C.c_void_p]
    try:
        pointer=fixes.XFixesGetCursorImage(display)
        if not pointer: raise RuntimeError('Cursor image unavailable')
        try:
            value=pointer.contents
            return dict(x=value.x,y=value.y,width=value.width,height=value.height,
                        visible_pixels=sum(bool(value.pixels[i]>>24) for i in range(value.width*value.height)))
        finally: x.XFree(pointer)
    finally: x.XCloseDisplay(display)


@app.command()
def main(output: Path = typer.Argument(...), seconds: int = typer.Option(20),
         solve: bool = typer.Option(False), mode: str = typer.Option('random')):
    if mode not in ('fixed', 'random'):
        raise ValueError('mode 必须为 fixed 或 random')
    output.mkdir(parents=True,exist_ok=True)
    config=json.loads(Path('/opt/ec26e/config/axu5ev/menu.json').read_text())
    config.update(plan_only=True,motion_enabled=False)
    config_path=output/'plan-only-menu.json'
    config_path.write_text(json.dumps(config))
    os.environ.update(EC_MENU_CONFIG=str(config_path),EC_MOTION_ENABLED='false')
    import tkinter as tk
    from ec_26_e.gui.board_menu import BoardMenu
    class TestMenu(BoardMenu):
        def write_status(self): pass  # Do not overwrite production service status.
    root=tk.Tk()
    menu=TestMenu(root)
    menu.display_directory=output/'display'
    root.lift()
    records={}
    started=time.monotonic()
    def preview():
        menu.choose(0 if mode == 'fixed' else 1)
        menu.action('preview','validation')
    def capture():
        records['preview']=json.loads(Path('/run/ec26e/preview-status.json').read_text())
        records['cursor']=cursor_info()
        framebuffer(output/'preview.png')
        records['ui_received_frames']=menu.camera.version
        if solve:
            menu.action('confirm','validation-plan-only')
            root.after(1000,finish_solve)
        else:
            finish()
    def finish_solve():
        if menu.model.busy or menu.transition is not None:
            if time.monotonic()-started>120:
                menu.action('cancel','validation-timeout')
            root.after(500,finish_solve)
            return
        menu.poll_prediction()
        framebuffer(output/'prediction.png')
        records.update(message=menu.model.message,prediction_state=menu.display_state,
                       prediction_token=menu.display_token)
        finish()
    def finish():
        records['elapsed_seconds']=time.monotonic()-started
        (output/'result.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
        menu.close()
    root.after(300,preview)
    root.after(seconds*1000,capture)
    root.mainloop()
    print(json.dumps(records,ensure_ascii=False))


if __name__=='__main__': app()
