from __future__ import annotations

import logging
import queue
from datetime import datetime, timedelta, timezone
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import get_settings


def _beijing_format_time(record: logging.LogRecord, tz, datefmt: str | None = None) -> str:
    dt = datetime.fromtimestamp(record.created, tz=tz)
    if datefmt:
        return dt.strftime(datefmt)
    return f"{dt.strftime('%Y-%m-%d %H:%M:%S')},{int(record.msecs):03d}"


def _beijing_tz():
    try:
        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return timezone(timedelta(hours=8))


_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# cost 专用：主线程/协程只入队，后台线程写盘，减轻高并发下文件锁与 I/O 阻塞
_cost_queue_listener: QueueListener | None = None


def _stop_cost_queue_listener() -> None:
    global _cost_queue_listener
    if _cost_queue_listener is not None:
        _cost_queue_listener.stop()
        _cost_queue_listener = None


def shutdown_cost_queue_logging() -> None:
    """应用退出时排空 cost 队列并停止写盘线程（见 main.py lifespan）。"""
    _stop_cost_queue_listener()


class BeijingFormatter(logging.Formatter):
    """日志时间 %(asctime)s 使用北京时间。"""

    def __init__(self, fmt: str | None = None, datefmt: str | None = None) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._tz = _beijing_tz()

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return _beijing_format_time(record, self._tz, datefmt)


def _patch_uvicorn_formatters() -> None:
    """Uvicorn 默认 access/error 格式不含 %(asctime)s，补上北京时间前缀。"""
    try:
        from uvicorn.logging import AccessFormatter, DefaultFormatter
    except ImportError:
        return

    class BeijingAccessFormatter(AccessFormatter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._tz = _beijing_tz()

        def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
            return _beijing_format_time(record, self._tz, datefmt)

    class BeijingDefaultFormatter(DefaultFormatter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._tz = _beijing_tz()

        def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
            return _beijing_format_time(record, self._tz, datefmt)

    access_fmt = '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    access_logger = logging.getLogger("uvicorn.access")
    for h in access_logger.handlers:
        old = h.formatter
        use_colors = getattr(old, "use_colors", None) if isinstance(old, AccessFormatter) else None
        h.setFormatter(BeijingAccessFormatter(fmt=access_fmt, use_colors=use_colors))

    default_fmt = "%(asctime)s %(levelprefix)s %(message)s"
    uvicorn_logger = logging.getLogger("uvicorn")
    for h in uvicorn_logger.handlers:
        old = h.formatter
        use_colors = getattr(old, "use_colors", None) if isinstance(old, DefaultFormatter) else None
        h.setFormatter(BeijingDefaultFormatter(fmt=default_fmt, use_colors=use_colors))


class DailySizeRotatingFileHandler(logging.Handler):
    """按北京时间分目录 logs/YYYY-MM-DD/YYYY-MM-DD-NN.log；单文件达上限后同一天递增 NN。"""

    def __init__(self, log_root: Path, max_bytes: int, encoding: str = "utf-8") -> None:
        super().__init__()
        self.terminator = "\n"
        self.log_root = log_root
        self.max_bytes = max_bytes
        self.encoding = encoding
        self._tz = _beijing_tz()
        self._stream = None
        self._path: Path | None = None
        self._date_open: str | None = None

    def _today_str(self) -> str:
        return datetime.now(self._tz).strftime("%Y-%m-%d")

    def _pick_path_for_today(self, today: str) -> Path:
        day_dir = self.log_root / today
        day_dir.mkdir(parents=True, exist_ok=True)
        sequences: list[tuple[int, Path]] = []
        for p in sorted(day_dir.glob(f"{today}-*.log")):
            try:
                seq_s = p.stem.rsplit("-", 1)[1]
                sequences.append((int(seq_s), p))
            except (IndexError, ValueError):
                continue
        if not sequences:
            return day_dir / f"{today}-01.log"
        sequences.sort(key=lambda x: x[0])
        last_seq, last_path = sequences[-1]
        try:
            if last_path.stat().st_size < self.max_bytes:
                return last_path
        except OSError:
            return last_path
        return day_dir / f"{today}-{last_seq + 1:02d}.log"

    def _next_numbered_path(self, current: Path) -> Path:
        date_part, seq_str = current.stem.rsplit("-", 1)
        next_seq = int(seq_str) + 1
        return current.parent / f"{date_part}-{next_seq:02d}.log"

    def _close_stream(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.flush()
            self._stream.close()
        except Exception:
            pass
        self._stream = None

    def _open_stream(self) -> None:
        if self._path is None:
            return
        self._stream = open(self._path, "a", encoding=self.encoding)

    def emit(self, record: logging.LogRecord) -> None:
        self.acquire()
        try:
            today = self._today_str()

            if self._date_open != today:
                self._close_stream()
                self._date_open = today
                self._path = self._pick_path_for_today(today)
                self._open_stream()

            assert self._path is not None
            assert self._stream is not None

            try:
                if self._path.exists() and self._path.stat().st_size >= self.max_bytes:
                    self._close_stream()
                    self._path = self._next_numbered_path(self._path)
                    self._open_stream()
                    assert self._stream is not None
            except OSError:
                pass

            msg = self.format(record)
            stream = self._stream
            stream.write(msg + self.terminator)
            stream.flush()
        finally:
            self.release()

    def close(self) -> None:
        self.acquire()
        try:
            self._close_stream()
        finally:
            self.release()
            super().close()


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    settings = get_settings()
    if not settings.LOG_FILE_ENABLED:
        _stop_cost_queue_listener()

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.LOG_LEVEL)

    formatter = BeijingFormatter(fmt=_LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if settings.LOG_FILE_ENABLED:
        log_dir = _PROJECT_ROOT / settings.LOG_FILE_DIR
        fh = DailySizeRotatingFileHandler(
            log_root=log_dir,
            max_bytes=int(settings.LOG_FILE_MAX_BYTES),
        )
        fh.setFormatter(formatter)
        root.addHandler(fh)

        _stop_cost_queue_listener()

        cost_logger = logging.getLogger("cost")
        cost_logger.handlers.clear()
        cost_logger.setLevel(settings.LOG_LEVEL)
        cost_logger.propagate = False
        cost_fh = DailySizeRotatingFileHandler(
            log_root=_PROJECT_ROOT / "logs" / "cost",
            max_bytes=int(settings.LOG_FILE_MAX_BYTES),
        )
        cost_fh.setFormatter(formatter)
        qmax = max(1, int(settings.LOG_COST_QUEUE_MAXSIZE))
        cost_queue: queue.Queue[logging.LogRecord] = queue.Queue(qmax)
        global _cost_queue_listener
        _cost_queue_listener = QueueListener(
            cost_queue, cost_fh, respect_handler_level=True,
        )
        _cost_queue_listener.start()
        cost_logger.addHandler(QueueHandler(cost_queue))

    _patch_uvicorn_formatters()

    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
