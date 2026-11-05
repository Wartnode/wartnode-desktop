"""HTTP budgets include opening, progressive bodies and cancellation.

The OS resolver cannot always be interrupted. At most four daemon openers may
remain inside it; callers still return at their deadline and fail closed when
that capacity is occupied. No request is retried automatically.
"""
from contextlib import contextmanager
import queue
import socket
import threading
import time

_OPENERS = threading.BoundedSemaphore(4)


def check(deadline, cancel=None):
    if cancel is not None and cancel.is_set():
        raise ValueError('Operation cancelled')
    if time.monotonic() >= deadline:
        raise ValueError('Network operation timed out')


class Response:
    def __init__(self, response, deadline, cancel, timeout):
        self.response, self.deadline, self.cancel = response, deadline, cancel
        self.timeout = timeout
        self.status = response.status
        self.finished = threading.Event()
        self.socket = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
        if self.socket is not None:
            threading.Thread(target=self.watch, daemon=True).start()

    def watch(self):
        while not self.finished.wait(.05):
            if time.monotonic() >= self.deadline or (self.cancel is not None and self.cancel.is_set()):
                try:self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                return

    def read1(self, size):
        check(self.deadline, self.cancel)
        # HTTPResponse closes its socket as soon as Content-Length is consumed.
        closed=getattr(self.response,'isclosed',None)
        if closed() if closed is not None else getattr(self.response,'closed',False):return b''
        if self.socket is not None:
            self.socket.settimeout(max(.001, min(self.timeout, self.deadline-time.monotonic())))
        # HTTPResponse.read1 performs at most one underlying buffered read;
        # read(size) may wait indefinitely while a peer keeps making progress.
        data = self.response.read1(size)
        check(self.deadline, self.cancel)
        return data

    def read_bounded(self, limit):
        result = bytearray()
        while True:
            block = self.read1(min(65536, limit-len(result)+1))
            if not block:return bytes(result)
            result.extend(block)
            if len(result)>limit:raise ValueError('Response exceeds size limit')


@contextmanager
def open_response(opener, request, deadline, cancel=None, timeout=3):
    check(deadline, cancel)
    if not _OPENERS.acquire(blocking=False):
        raise ValueError('Network is busy. Try again shortly.')
    abandoned = threading.Event()
    replies = queue.Queue(maxsize=1)

    def open_request():
        response = None
        try:
            response = opener.open(request, timeout=max(.001, min(timeout, deadline-time.monotonic())))
            replies.put((response, None))
        except Exception as error:
            replies.put((None, error))
        finally:
            _OPENERS.release()
            # The queue hand-off can race cancellation. The event and queue
            # are checked again by the caller's finally block as well.
            if abandoned.is_set() and response is not None:response.close()

    threading.Thread(target=open_request, daemon=True).start()
    response = budget = None
    try:
        while True:
            check(deadline, cancel)
            try:
                response, error = replies.get(timeout=min(.05, max(.001, deadline-time.monotonic())))
                break
            except queue.Empty:pass
        if error:raise error
        budget = Response(response, deadline, cancel, timeout)
        check(deadline, cancel)
        yield budget
    finally:
        abandoned.set()
        if budget is not None:budget.finished.set()
        if response is not None:response.close()
        else:
            try:
                late, _ = replies.get_nowait()
                if late is not None:late.close()
            except queue.Empty:pass
