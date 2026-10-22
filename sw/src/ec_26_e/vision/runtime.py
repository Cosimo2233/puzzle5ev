"""菜单空闲时加载视觉查找表；限制 CPU 线程避免挤占取帧和界面。"""
import os

import cv2
import numpy as np


def prepare_vision_runtime():
    threads = int(os.environ.get('EC_VISION_THREADS', '1'))
    if not 1 <= threads <= 4:
        raise ValueError('EC_VISION_THREADS 必须为 1～4')
    cv2.setNumThreads(threads)
    cv2.cvtColor(np.zeros((8, 8, 3), np.uint8), cv2.COLOR_BGR2LAB)
