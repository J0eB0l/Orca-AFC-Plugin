"""The plugin's own thread, shared by the AFC tab, the G-code step and the
printer agent.

OrcaSlicer checks what a plugin does while it runs OrcaSlicer's calls (network,
files, threads) and asks the user, and off its UI thread it refuses outright.
Work started from the plugin's own thread is not checked, so everything that
talks to the printer runs here, one job at a time.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback


class WorkerTimeout(Exception):
    pass


class Worker:
    """One thread that runs jobs in order, so the network never holds up
    OrcaSlicer's window. It starts on the first job and again after stop().
    With `tick`, it also calls tick() whenever it has been idle `every` seconds."""

    def __init__(self, name: str = "AFC plugin", tick=None, every: float = 5.0):
        self._name = name
        self._jobs = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()
        self._tick = tick
        self._every = every

    def submit(self, job) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._jobs = queue.Queue()
                self._thread = threading.Thread(target=self._run, args=(self._jobs,), name=self._name, daemon=True)
                self._thread.start()
            self._jobs.put(job)

    def call(self, fn, timeout: float):
        """Run fn on the worker and return what it returns (or raise what it
        raises). Raises WorkerTimeout when it takes longer than `timeout`; the
        job still finishes on the worker."""
        if self.on_worker():
            return fn()
        done = threading.Event()
        answer = {}

        def job():
            try:
                answer["value"] = fn()
            except BaseException as error:  # handed back to the caller
                answer["error"] = error
            finally:
                done.set()

        self.submit(job)
        if not done.wait(timeout):
            raise WorkerTimeout("the printer did not answer in %d s" % timeout)
        if "error" in answer:
            raise answer["error"]
        return answer.get("value")

    def on_worker(self) -> bool:
        return self._thread is not None and threading.current_thread() is self._thread

    def stop(self) -> None:
        """Let the thread end after the jobs it has; a later submit starts a new one."""
        with self._lock:
            if self._thread is not None:
                self._jobs.put(None)
            self._thread = None
            self._jobs = queue.Queue()

    def _run(self, jobs) -> None:
        due = time.monotonic() + self._every
        while True:
            try:
                job = jobs.get(timeout=max(0.0, due - time.monotonic()) if self._tick else None)
            except queue.Empty:
                job, due = self._tick, time.monotonic() + self._every
            if job is None:
                return
            try:
                job()
            except Exception:  # a job reports its own errors; this keeps the thread alive
                traceback.print_exc()


_shared = Worker()


def shared() -> Worker:
    return _shared
