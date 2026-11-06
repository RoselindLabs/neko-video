"""Durable unresolved sends, sharing the bounded transaction/clock implementation.

Only an authoritative success removes an admission. There is no expiry, replay,
or user-facing reset. Successful intentional repeated shares are permitted.
"""

from .analysis_store import AnalysisStore
from .media import MediaError


class DeliveryStore(AnalysisStore):
    def confirmed(self, key):
        with self.transaction() as (con, _):
            changed = con.execute(
                "DELETE FROM analysis WHERE id=? AND state='unknown'", (key,)
            ).rowcount
            if changed != 1:
                raise MediaError("发送记录状态异常；保留现场，不自动重发")
            con.execute("DELETE FROM analysis_identity WHERE id=?", (key,))
