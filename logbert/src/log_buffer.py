"""
Log Buffer for Batch Processing
Collects streaming logs into small batches for efficient processing
"""
import asyncio
import time
from typing import List, Dict, Any, Callable, Optional
from datetime import datetime


class LogBuffer:
    """
    Buffer for collecting streaming logs into batches

    Flushes when either:
    - Buffer reaches max_size entries, OR
    - max_wait seconds have elapsed since last flush
    """

    def __init__(self, max_size: int = 50, max_wait: float = 2.0):
        """
        Initialize log buffer

        Args:
            max_size: Maximum number of logs before auto-flush
            max_wait: Maximum seconds to wait before auto-flush
        """
        self.max_size = max_size
        self.max_wait = max_wait

        self.buffer: List[Dict[str, Any]] = []
        self.last_flush = time.time()
        self.flush_lock = asyncio.Lock()

        # Callback function (set by user)
        self.on_flush: Optional[Callable[[List[Dict]], Any]] = None

        # Start background timer task
        self._timer_task = None

    def start_timer(self):
        """Start background timer for auto-flush"""
        if self._timer_task is None:
            self._timer_task = asyncio.create_task(self._timer_loop())

    async def _timer_loop(self):
        """Background task to check for timeout-based flush"""
        while True:
            await asyncio.sleep(0.5)  # Check twice per second

            # Check if we need to flush due to timeout
            if self.buffer and (time.time() - self.last_flush >= self.max_wait):
                await self.flush()

    async def add(self, log: Dict[str, Any]):
        """
        Add a log entry to the buffer

        Args:
            log: Log entry dict with timestamp, labels, message
        """
        self.buffer.append(log)

        # Flush if buffer is full
        if len(self.buffer) >= self.max_size:
            await self.flush()

    async def flush(self):
        """Flush buffer and call user callback"""
        async with self.flush_lock:
            if not self.buffer:
                return

            if self.on_flush is None:
                print(f"  Warning: No flush callback set, discarding {len(self.buffer)} logs")
                self.buffer = []
                return

            # Get current buffer and clear
            logs_to_process = self.buffer.copy()
            self.buffer = []
            self.last_flush = time.time()

            # Call user callback
            try:
                print(f"[{datetime.now()}] Flushing {len(logs_to_process)} logs...")
                await self.on_flush(logs_to_process)
            except Exception as e:
                print(f"  Error in flush callback: {e}")
                import traceback
                traceback.print_exc()

    async def stop(self):
        """Stop the timer task and flush remaining logs"""
        if self._timer_task:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass

        # Final flush
        await self.flush()

    def __len__(self):
        """Return current buffer size"""
        return len(self.buffer)
