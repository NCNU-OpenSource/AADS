from datetime import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from main import _next_cursor_timestamp


def test_next_cursor_timestamp_uses_newest_fetched_log():
    poll_end = datetime(2026, 6, 2, 3, 40, 0)
    first = datetime(2026, 6, 2, 3, 38, 41)
    second = datetime(2026, 6, 2, 3, 38, 42)

    assert _next_cursor_timestamp(
        [{"timestamp": first}, {"timestamp": second}],
        poll_end,
    ) == second


def test_next_cursor_timestamp_falls_back_without_log_timestamps():
    poll_end = datetime(2026, 6, 2, 3, 40, 0)

    assert _next_cursor_timestamp([{"message": "no timestamp"}], poll_end) == poll_end
