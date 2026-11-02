from collections import Counter
from datetime import UTC, datetime


def _today():
    return datetime.now(UTC).date()


def client_ip(request) -> str:
    # The last address is the one Caddy saw, it overwrites what the client sent and port 8000 is closed to the outside.
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[-1].strip() if forwarded else request.client.host


class IpCounter:
    """Counts per address for the current UTC day.

    shortcut: lives in one uvicorn process and resets on deploy, move to the database when the app runs several processes.
    """

    def __init__(self):
        self._day = None
        self._counts = Counter()

    def _roll(self):
        if self._day != (today := _today()):
            self._day = today
            self._counts.clear()

    def reached(self, ip, limit) -> bool:
        self._roll()
        return self._counts[ip] >= limit

    def add(self, ip) -> None:
        self._roll()
        self._counts[ip] += 1

    def remove(self, ip) -> None:
        # Guarded: a day change between add and remove must not leave a negative count.
        self._roll()
        if self._counts[ip] > 0:
            self._counts[ip] -= 1
