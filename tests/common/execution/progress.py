"""Best-effort progress reads; budget admission remains authoritative/fail-closed."""
import sqlite3
import time


class ProgressBudget:
    def __init__(self, ledger):
        self.ledger = ledger
        self.last = None
        self.updated = None

    def read(self):
        try:
            snapshot = self.ledger.summary()
        except sqlite3.OperationalError as error:
            code = getattr(error, 'sqlite_errorcode', None)
            if (code is not None and (code & 255) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or
                    code is None and str(error) not in ('database is locked', 'database table is locked')):
                raise
            return self.last, {'status': 'stale' if self.last is not None else 'unavailable',
                               'updated': self.updated, 'error_type': 'sqlite_busy',
                               'message': 'Progress read deferred; next heartbeat will retry'}
        self.last = snapshot
        self.updated = time.time()
        return snapshot, {'status': 'fresh', 'updated': self.updated}
