import threading

import pandas as pd

from fbm.analytics.context import AnalyticsContext
from fbm.analytics.valuation import PoolStats, ValuationConfig
from fbm.store import db


def test_models_from_another_thread_with_fresh_connection(tmp_path):
    path = tmp_path / "fbm.db"
    main_conn = db.connect(path)
    ctx = AnalyticsContext(
        main_conn, 2027, 2026, pd.DataFrame(), pd.DataFrame(), PoolStats({}, {}, {}, {}, {}, []), ValuationConfig()
    )
    errors: list[BaseException] = []
    results: list[dict] = []

    def worker() -> None:
        try:
            results.append(ctx.models(["123"], db.connect(path)))
        except BaseException as err:  # noqa: BLE001
            errors.append(err)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert not errors and "123" in results[0]
