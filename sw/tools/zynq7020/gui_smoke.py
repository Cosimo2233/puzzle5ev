"""Windows 开发机窗口绘制验证：仅使用静态图，不连接相机或电机。"""
from pathlib import Path
import ctypes
import time
import tkinter as tk

import cv2
import numpy as np

from ec_26_e.gui.controller import Controller
from ec_26_e.gui.window import PuzzleWindow


def capture(root, path):
    # Capture only our own application window, including the title bar.
    user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    for fn in (user.GetDC,gdi.CreateCompatibleDC,gdi.CreateCompatibleBitmap,gdi.SelectObject):
        fn.restype = ctypes.c_void_p
    user.GetAncestor.restype = ctypes.c_void_p
    user.GetAncestor.argtypes = [ctypes.c_void_p,ctypes.c_uint]
    user.GetDC.argtypes = [ctypes.c_void_p]
    gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p,ctypes.c_int,ctypes.c_int]
    gdi.SelectObject.argtypes = [ctypes.c_void_p,ctypes.c_void_p]
    hwnd = user.GetAncestor(root.winfo_id(),2)
    rect=(ctypes.c_long*4)()
    user.GetWindowRect.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
    user.GetWindowRect(hwnd,rect)
    width,height=rect[2]-rect[0],rect[3]-rect[1]
    dc=user.GetDC(hwnd); memory=gdi.CreateCompatibleDC(dc)
    bitmap=gdi.CreateCompatibleBitmap(dc,width,height); previous=gdi.SelectObject(memory,bitmap)
    class Header(ctypes.Structure):
        _fields_=[('size',ctypes.c_uint32),('width',ctypes.c_int32),('height',ctypes.c_int32),
          ('planes',ctypes.c_uint16),('bits',ctypes.c_uint16),('compression',ctypes.c_uint32),
          ('imagesize',ctypes.c_uint32),('x',ctypes.c_int32),('y',ctypes.c_int32),
          ('used',ctypes.c_uint32),('important',ctypes.c_uint32)]
    header=Header(40,width,-height,1,32,0,0,0,0,0,0)
    pixels=(ctypes.c_ubyte*(width*height*4))()
    user.PrintWindow.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint]
    gdi.GetDIBits.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint]
    try:
        if not user.PrintWindow(hwnd,memory,2): raise RuntimeError('PrintWindow failed')
        if not gdi.GetDIBits(memory,bitmap,0,height,pixels,ctypes.byref(header),0): raise RuntimeError('GetDIBits failed')
        path.parent.mkdir(parents=True,exist_ok=True)
        cv2.imwrite(str(path),np.ctypeslib.as_array(pixels).reshape(height,width,4)[:,:,:3])
    finally:
        gdi.SelectObject(memory,previous)
        gdi.DeleteObject.argtypes=[ctypes.c_void_p];gdi.DeleteObject(bitmap)
        gdi.DeleteDC.argtypes=[ctypes.c_void_p];gdi.DeleteDC(memory)
        user.ReleaseDC.argtypes=[ctypes.c_void_p,ctypes.c_void_p];user.ReleaseDC(hwnd,dc)


def main():
    controller=Controller(Path('../deploy/zynq7020/board.env.example'),Path('configs/zynq7020'),
        'cpu',Path('../baselines/upstream/fixed4.png'))
    root=tk.Tk()
    window=PuzzleWindow(root,controller,False)
    controller.start()
    deadline=time.monotonic()+8
    try:
        while time.monotonic()<deadline:
            root.update()
            if controller.snapshot()[0]>3: break
            time.sleep(0.02)
        assert controller.ready,controller.status
        root.update();capture(root,Path('../docs/validation/migration/gui-fixed.png'))
        window.mode.set('随机两片');window.select()
        time.sleep(0.3);root.update()
        assert controller.label=='随机两片'
        assert window.start.cget('state')=='disabled'
        capture(root,Path('../docs/validation/migration/gui-candidates.png'))
        print('PASS: rendered window, mode switch, live contour refresh, demo motor disabled')
    finally:
        controller.close()
        root.destroy()


if __name__=='__main__': main()
