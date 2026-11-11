from __future__ import annotations

import pandas as pd

from app.schemas.records import MarketBar


def bars_to_frame(bars: list[MarketBar]) -> pd.DataFrame:
    cols = ["session_date", "open", "high", "low", "close", "adj_close", "volume"]
    if not bars:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame([{c: getattr(b, c) for c in cols} for b in bars])
    df["session_date"] = pd.to_datetime(df["session_date"])
    return df.sort_values("session_date").drop_duplicates("session_date", keep="last").reset_index(drop=True)
