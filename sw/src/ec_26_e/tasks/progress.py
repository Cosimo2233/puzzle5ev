"""长时间计算阶段的日志提示，不访问相机或运动硬件。"""
from __future__ import annotations

from contextlib import contextmanager
import logging
import threading
import time

logger = logging.getLogger(__name__)


@contextmanager
def report_stage(label: str):
    """输出阶段开始、五秒心跳及结果；退出时结束提示线程。"""
    started = time.perf_counter()
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(5.0):
            logger.info("%s：仍在计算，已用 %.1f 秒", label,
                        time.perf_counter() - started)

    logger.info("%s", label)
    worker = threading.Thread(target=heartbeat, name='task-progress', daemon=True)
    worker.start()
    try:
        yield
    except BaseException:
        logger.info("%s：中止，耗时 %.1f 秒", label, time.perf_counter() - started)
        raise
    else:
        logger.info("%s：完成，耗时 %.1f 秒", label, time.perf_counter() - started)
    finally:
        stopped.set()
        worker.join()
