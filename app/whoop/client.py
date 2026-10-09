from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Iterator

import httpx

from app.whoop import constants as C
from app.whoop.auth import get_access_token

log = logging.getLogger(__name__)


class WhoopError(RuntimeError):
    pass


class WhoopClient:
    """Thin client over the Whoop v2 developer API with pagination and retries."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._client = httpx.Client(base_url=C.API_V2, timeout=timeout)
        self._token: str | None = None

    def __enter__(self) -> "WhoopClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- plumbing -------------------------------------------------------

    def _headers(self, refresh: bool = False) -> dict[str, str]:
        if self._token is None or refresh:
            self._token = get_access_token(force_refresh=refresh)
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, path: str, params: dict[str, Any] | None = None) -> dict:
        for attempt in range(5):
            response = self._client.get(path, params=params, headers=self._headers())

            if response.status_code == 401 and attempt < 4:
                # Token went stale mid-run; force one refresh and retry.
                self._headers(refresh=True)
                continue

            if response.status_code == 429:
                wait = float(response.headers.get("Retry-After", 5))
                log.warning("Whoop rate limit hit, sleeping %.1fs", wait)
                time.sleep(wait)
                continue

            if response.status_code >= 500 and attempt < 4:
                time.sleep(2 ** attempt)
                continue

            if response.status_code >= 400:
                raise WhoopError(
                    f"{response.status_code} on {path}: {response.text[:400]}"
                )
            return response.json()

        raise WhoopError(f"Не удалось получить {path} после нескольких попыток")

    def _paginate(
        self,
        path: str,
        start: dt.datetime | None = None,
        end: dt.datetime | None = None,
        max_records: int | None = None,
    ) -> Iterator[dict]:
        params: dict[str, Any] = {"limit": C.PAGE_LIMIT}
        if start is not None:
            params["start"] = _iso(start)
        if end is not None:
            params["end"] = _iso(end)

        seen = 0
        next_token: str | None = None
        while True:
            if next_token:
                params["nextToken"] = next_token
            payload = self._request(path, params)
            records = payload.get("records") or []
            for record in records:
                yield record
                seen += 1
                if max_records is not None and seen >= max_records:
                    return
            next_token = payload.get("next_token")
            if not next_token:
                return

    # --- collections ----------------------------------------------------

    def cycles(self, start=None, end=None, max_records=None) -> Iterator[dict]:
        return self._paginate(C.EP_CYCLE, start, end, max_records)

    def sleeps(self, start=None, end=None, max_records=None) -> Iterator[dict]:
        return self._paginate(C.EP_SLEEP, start, end, max_records)

    def recoveries(self, start=None, end=None, max_records=None) -> Iterator[dict]:
        return self._paginate(C.EP_RECOVERY, start, end, max_records)

    def workouts(self, start=None, end=None, max_records=None) -> Iterator[dict]:
        return self._paginate(C.EP_WORKOUT, start, end, max_records)

    # --- singletons -----------------------------------------------------

    def profile(self) -> dict:
        return self._request(C.EP_PROFILE)

    def body_measurement(self) -> dict:
        return self._request(C.EP_BODY)


def _iso(moment: dt.datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
