"""Tk 操作面板；仅主线程访问窗口，PPM 图像无需额外图像 GUI 依赖。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import cv2

from ec_26_e.gui.controller import MODES


class PuzzleWindow:
    def __init__(self, root, controller, fullscreen=True):
        self.root, self.controller = root, controller
        self.version = -1
        self.closing = False
        root.title("几何拼图 · FPGA 操作台")
        root.geometry("1280x720")
        root.minsize(880,560)
        root.configure(bg="#101926")
        root.attributes("-fullscreen", fullscreen)
        root.bind("<F11>", lambda _: root.attributes("-fullscreen", not root.attributes("-fullscreen")))
        root.bind("<Escape>", lambda _: root.attributes("-fullscreen", False))
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TCombobox", font=("Microsoft YaHei",14), padding=9)
        header = tk.Frame(root,bg="#101926")
        header.pack(fill="x",padx=24,pady=(18,12))
        tk.Label(header,text="几何拼图操作台",fg="#f1f5fa",bg="#101926",
                 font=("Microsoft YaHei",24,"bold")).pack(side="left")
        tk.Label(header,text="7020  /  HDMI",fg="#65d9b3",bg="#101926",
                 font=("Arial",14)).pack(side="right")
        content = tk.Frame(root,bg="#101926")
        content.pack(fill="both",expand=True,padx=24)
        self.video = tk.Label(content,bg="#080e16",fg="#90a4ba",text="正在连接摄像头…",
                              font=("Microsoft YaHei",18))
        self.video.pack(side="left",fill="both",expand=True)
        panel = tk.Frame(content,bg="#1a2839",width=280)
        panel.pack(side="right",fill="y",padx=(16,0))
        panel.pack_propagate(False)
        tk.Label(panel,text="运行模式",bg="#1a2839",fg="white",font=("Microsoft YaHei",16)).pack(anchor="w",padx=18,pady=(24,12))
        self.mode = ttk.Combobox(panel,values=list(MODES),state="readonly",width=15)
        self.mode.set(controller.label)
        self.mode.pack(fill="x",padx=18)
        self.mode.bind("<<ComboboxSelected>>",self.select)
        tk.Label(panel,text="绿色线：有效纸片边界\n黄色线：待确认轮廓\n顶点随当前画面更新\n\n预览不会驱动电机",
                 justify="left",bg="#1a2839",fg="#adbed0",font=("Microsoft YaHei",12)).pack(anchor="w",padx=18,pady=24)
        self.start = tk.Button(panel,text="开始拼图",command=self.run,bg="#47cba2",fg="#081b17",
                               font=("Microsoft YaHei",17,"bold"),relief="flat",pady=12,state="disabled")
        self.start.pack(fill="x",padx=18)
        tk.Label(panel,text="开始后将控制实际机构。\n运行中禁止切换模式或退出。",
                 justify="left",bg="#1a2839",fg="#adbed0",font=("Microsoft YaHei",11)).pack(anchor="w",padx=18,pady=14)
        self.exit = tk.Button(panel,text="退出操作台",command=self.close,bg="#2b3e54",fg="white",
                              relief="flat",pady=10,font=("Microsoft YaHei",12))
        self.exit.pack(side="bottom",fill="x",padx=18,pady=20)
        self.status = tk.Label(root,text="初始化中",anchor="w",fg="#eef5fc",bg="#101926",font=("Microsoft YaHei",13),wraplength=1180)
        self.status.pack(fill="x",padx=24,pady=(12,2))
        self.detail = tk.Label(root,text="",anchor="w",fg="#9aadc1",bg="#101926",font=("Microsoft YaHei",10))
        self.detail.pack(fill="x",padx=24,pady=(0,12))
        root.after(50,self.poll)

    def select(self, _=None):
        try:
            self.controller.select(self.mode.get())
        except (ValueError,RuntimeError) as error:
            self.controller._status(str(error))

    def run(self):
        try:
            self.controller.run()
        except (ValueError,RuntimeError,OSError) as error:
            self.controller._status(str(error))

    def close(self):
        if self.controller.request_close():
            self.closing = True
            self.status.configure(text="正在释放相机与图像处理器…")
        else:
            self.status.configure(text="本次任务仍在运行，请完成后退出；实体急停仍由机构硬件负责。")

    def poll(self):
        if self.closing and not self.controller.worker.is_alive():
            self.root.destroy()
            return
        version,image,status,detail,ready,busy = self.controller.snapshot()
        if not self.closing:
            self.status.configure(text=status)
            self.detail.configure(text=detail)
        self.start.configure(state="normal" if ready and not busy and not self.closing and self.controller.demo is None and self.controller.motion_enabled else "disabled")
        self.mode.configure(state="disabled" if busy or self.closing else "readonly")
        self.exit.configure(state="disabled" if busy or self.closing else "normal")
        if version != self.version:
            self.version = version
            if image is None:
                self.video.configure(image="",text="暂无有效实时画面")
                self.photo = None
            else:
                h,w = image.shape[:2]
                scale = min(max(1,self.video.winfo_width())/w,max(1,self.video.winfo_height())/h)
                shown = cv2.resize(image,(max(1,int(w*scale)),max(1,int(h*scale))))
                rgb = cv2.cvtColor(shown,cv2.COLOR_BGR2RGB)
                header = f"P6\n{rgb.shape[1]} {rgb.shape[0]}\n255\n".encode()
                self.photo = tk.PhotoImage(data=header+rgb.tobytes(),format="PPM")
                self.video.configure(image=self.photo,text="")
        self.root.after(50,self.poll)


def launch(controller, fullscreen=True):
    root = tk.Tk()
    PuzzleWindow(root,controller,fullscreen)
    controller.start()
    try:
        root.mainloop()
    finally:
        controller.close()
