"""Validate incoming series and verify stored samples against separate official queries."""
import argparse
from datetime import datetime
from decimal import Decimal
from io import StringIO
import json
import math
import sys

import pandas as pd

from data_fetcher import BRT, ROOT, SERIES, daily_url, get, read_snapshot, read_state, session

AGGREGATES = "https://servicodados.ibge.gov.br/api/v3/agregados"
PTAX = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata"


def validate(key, frame, previous, today):
    series = SERIES[key]
    dates = frame["date"]
    if len(frame) < 2 or dates.isna().any() or dates.duplicated().any():
        raise ValueError("Empty, insufficient, or duplicate observations")
    if dates.min().date() != series.first or dates.max().date() > today:
        raise ValueError(f"Unexpected coverage: {dates.min()} to {dates.max()}")
    if not frame["value"].map(math.isfinite).all() or not frame["value"].between(*series.bounds).all():
        raise ValueError("Missing, non-finite, or out-of-range values")
    if series.period in {"M", "Q"}:
        periods = dates.dt.to_period(series.period)
        if list(periods) != list(pd.period_range(periods.min(), periods.max(), freq=series.period)):
            raise ValueError("Missing monthly or quarterly observations")
    if not previous.empty and not previous["date"].isin(dates).all():
        raise ValueError("Source omitted observations from the prior validated snapshot")
    # Prevent a truncated daily API response from being adopted on the first run.
    if series.period == "D":
        if (dates.diff().dropna().dt.days > 10).any():
            raise ValueError("Unexpected gap in daily observations")
        if (today - dates.max().date()).days > 10:
            raise ValueError("Daily series ends more than ten days before the refresh date")
    return "Passed: coverage, dates, unique keys, finite values, and bounds"


def reference(client, key, observed, sample):
    series = SERIES[key]
    if series.table:
        code = {
            "M": observed.strftime("%Y%m"),
            "Q": f"{observed.year}{sample.quarter:02}",
            "Y": str(observed.year),
        }[series.period]
        url = (f"{AGGREGATES}/{series.table}/periodos/{code}/variaveis/{series.code}"
               "?localidades=N1[1]")
        if series.categories:
            url += "&classificacao=" + "|".join(
                f"{dimension}[{category}]" for dimension, category in series.categories)
        payload = get(client, url).json()[0]
        result, = payload["resultados"]
        actual = {item["id"]: set(item["categoria"]) for item in result["classificacoes"]}
        expected = {str(d): {str(c)} for d, c in series.categories}
        item, = result["series"]
        if (str(payload["id"]) != series.code or actual != expected
                or str(item["localidade"]["id"]) != "1"
                or payload["unidade"] != ("Anos" if series.period == "Y" else "%")):
            raise ValueError("Unexpected official reference metadata")
        return Decimal(item["serie"][code]), url, "IBGE Aggregates API, independent query and parser"
    if key == "exchange":
        url = (f"{PTAX}/CotacaoDolarDia(dataCotacao=@dataCotacao)"
               f"?@dataCotacao='{observed:%m-%d-%Y}'&$format=json")
        item, = get(client, url).json()["value"]
        if item["dataHoraCotacao"][:10] != observed.isoformat():
            raise ValueError("PTAX returned a different date")
        return Decimal(str(item["cotacaoVenda"])), url, "BCB PTAX closing selling quote"
    url = daily_url(series, observed, observed, "csv")
    raw = get(client, url).content.decode("utf-8-sig")
    frame = pd.read_csv(StringIO(raw), sep=";", dtype=str)
    row, = frame.itertuples(index=False, name=None)
    if row[0] != observed.strftime("%d/%m/%Y"):
        raise ValueError("SGS CSV returned a different date")
    return Decimal(row[1].replace(",", ".")), url, "BCB SGS date-specific CSV query and separate parser"


def verify():
    state = read_state()
    snapshot = read_snapshot(state=state)
    if snapshot.empty:
        raise SystemExit("Run data_fetcher.py successfully before verification.")
    # Past daily observations avoid same-day differences in publication times.
    checked = datetime.now(BRT)
    records = []
    requests_log = []
    with session() as client:
        client.hooks["response"].append(lambda response, *args, **kwargs: requests_log.append(response.url))
        for key, series in SERIES.items():
            rows = snapshot.loc[snapshot["indicator"].eq(key)]
            row = rows.iloc[-2 if series.period == "D" else -1]
            observed = row["date"].date()
            retrieved = Decimal(str(row["value"]))
            entry = dict(indicator=series.name, observation_date=observed.isoformat(),
                         dashboard_value=str(retrieved), source_value="", difference="",
                         source_url=series.link, verification_date=checked.date().isoformat(),
                         status="Unverified", method="", snapshot=state["snapshot"])
            count = len(requests_log)
            try:
                source, url, method = reference(client, key, observed, row["date"])
                difference = retrieved - source
                entry.update(source_value=str(source), difference=str(difference),
                             status="Verified" if difference == 0 else "Mismatch",
                             source_url=url, method=method)
            except Exception as exc:
                entry["method"] = str(exc)
                if len(requests_log) > count:
                    entry["source_url"] = requests_log[-1]
            records.append(entry)
    path = ROOT / "verification/verification.csv"
    path.parent.mkdir(exist_ok=True)
    pd.DataFrame(records).to_csv(path, index=False)
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return all(entry["status"] == "Verified" for entry in records)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    sys.exit(not verify())
