"""Retrieve and atomically publish a validated, versioned economic snapshot."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
from uuid import uuid4
from zoneinfo import ZoneInfo

from filelock import FileLock
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
BRT = ZoneInfo("America/Sao_Paulo")
START = date(2012, 1, 1)
SIDRA = "https://apisidra.ibge.gov.br/values/t"
SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs"
TABLE = "https://sidra.ibge.gov.br/tabela"
BCB = "https://dadosabertos.bcb.gov.br/dataset"


@dataclass(frozen=True)
class Series:
    name: str
    definition: str
    unit: str
    frequency: str
    agency: str
    link: str
    code: str
    color: str
    precision: int
    bounds: tuple
    first: date
    table: int | None = None
    categories: tuple = ()
    period: str = "D"
    note: str = "Official values as published."


SERIES = {
    "inflation": Series("IPCA inflation", "Consumer-price inflation accumulated over the preceding 12 months.",
                        "%", "Monthly", "IBGE", f"{TABLE}/1737", "2265", "#007878", 2,
                        (-100, 100), date(2012, 1, 31), 1737, period="M",
                        note="IBGE publishes the 12-month change directly."),
    "gdp": Series("Real GDP growth", "Change in GDP volume from the same quarter one year earlier.",
                  "% YoY", "Quarterly", "IBGE", f"{TABLE}/5932", "6561", "#244d90", 1,
                  (-100, 100), date(2012, 3, 31), 5932, ((11255, 90707),), "Q",
                  "Published year-over-year rate from the GDP volume index."),
    "selic": Series("Selic target", "Copom’s annualized policy-rate target.",
                    "% per year", "Daily", "Banco Central do Brasil",
                    f"{BCB}/432-taxa-de-juros---meta-selic-definida-pelo-copom", "432",
                    "#a44d12", 2, (0, 100), START),
    "unemployment": Series("Unemployment", "Share of the labor force aged 14+ that is unemployed.",
                           "%", "Monthly, rolling quarter", "IBGE", f"{TABLE}/6381", "4099",
                           "#775494", 1, (0, 100), date(2012, 3, 31), 6381, period="M",
                           note="Official three-month rolling estimate, dated to the final month. First period: Jan–Mar 2012."),
    "exchange": Series("USD exchange rate", "Brazilian reais per US dollar, BCB’s daily selling quote.",
                       "BRL/USD", "Daily, trading days", "Banco Central do Brasil",
                       f"{BCB}/1-taxa-de-cambio---livre---dolar-americano-venda---diario", "1",
                       "#366b52", 4, (0.0001, 100), date(2012, 1, 2),
                       note="Higher values indicate depreciation of the real against the US dollar."),
    "education": Series("Mean years of schooling", "Average years of schooling for Brazil’s population aged 25+.",
                        "years", "Annual", "IBGE", f"{TABLE}/7126", "3593", "#08766b", 1,
                        (0, 35), date(2016, 12, 31), 7126, ((2, 6794), (58, 108866)), "Y",
                        "PNAD Contínua education module, both sexes. Comparable series starts in 2016. "
                        "Only published years are plotted and missing years are left blank."),
}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temp = Path(file.name)
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp.replace(path)


def session():
    client = requests.Session()
    client.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504],
        respect_retry_after_header=False,
    )))
    return client


def get(client, url):
    response = client.get(url, timeout=(8, 25))
    response.raise_for_status()
    return response


def sidra_url(series, periods="all"):
    filters = "".join(f"/c{dimension}/{category}" for dimension, category in series.categories)
    return f"{SIDRA}/{series.table}/n1/all/v/{series.code}/p/{periods}{filters}"


def daily_url(series, start, end, fmt="json"):
    return (f"{SGS}.{series.code}/dados?formato={fmt}"
            f"&dataInicial={start:%d/%m/%Y}&dataFinal={end:%d/%m/%Y}")


def sidra_date(code, frequency):
    year = int(code[:4])
    part = int(code[4:]) if len(code) > 4 else 0
    month = {"Y": 12, "Q": part * 3, "M": part}[frequency]
    return pd.Period(f"{year}-{month:02}", freq="M").end_time.date()


def fetch(key, today, entry):
    series = SERIES[key]
    rows = []
    with session() as client:
        if series.table:
            url = sidra_url(series)
            entry["urls"].append(url)
            payload = get(client, url).json()
            if not isinstance(payload, list) or not payload or payload[0].get("V") != "Valor":
                raise ValueError("Unexpected SIDRA response")
            for row in payload[1:]:
                observed = sidra_date(row["D3C"], series.period)
                if not START <= observed <= today:
                    continue
                expected = [("D1C", "1"), ("D2C", series.code),
                            ("MN", "Anos" if series.period == "Y" else "%")]
                expected.extend((f"D{index}C", str(category))
                                for index, (_, category) in enumerate(series.categories, 4))
                if any(row[field] != value for field, value in expected):
                    raise ValueError("Unexpected territory, variable, unit, or category")
                if series.period == "Y" and row["V"] in {"..", "...", "X", "-"}:
                    continue
                rows.append((observed, row["V"]))
        else:
            start = START
            while start <= today:
                end = min(today, date(start.year + 5, 1, 1) - timedelta(days=1))
                url = daily_url(series, start, end)
                entry["urls"].append(url)
                for row in get(client, url).json():
                    observed = datetime.strptime(row["data"], "%d/%m/%Y").date()
                    if not start <= observed <= end:
                        raise ValueError("SGS returned a date outside the requested interval")
                    rows.append((observed, row["valor"]))
                start = end + timedelta(days=1)
    frame = pd.DataFrame(rows, columns=["date", "value"])
    entry["observations"] = len(frame)
    if not frame.empty:
        entry["latest"] = max(frame["date"]).isoformat()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["value"] = pd.to_numeric(frame["value"], errors="raise")
    return frame.sort_values("date").reset_index(drop=True)


def read_state(root=ROOT):
    """A single atomic pointer selects both the snapshot and its refresh status."""
    path = root / "data/state.json"
    return json.loads(path.read_text()) if path.exists() else {}


def read_snapshot(root=ROOT, state=None):
    if state is None:
        state = read_state(root)
    snapshot = state.get("snapshot")
    if snapshot is None:
        return pd.DataFrame(columns=["indicator", "date", "value"])
    return pd.read_csv(root / snapshot, parse_dates=["date"])


def refresh(root=ROOT):
    # Local sessions share one writer. GitHub Actions serializes remote runs.
    from verify_data import validate

    directory = root / "data"
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(directory / ".refresh.lock", timeout=240):
        started = datetime.now(BRT)
        today = started.date()
        state = read_state(root)
        previous = read_snapshot(root, state)
        entries = {key: dict(source=series.agency, urls=[], observations=None,
                             latest=None, validation="Pending")
                   for key, series in SERIES.items()}
        frames = {}
        with ThreadPoolExecutor(max_workers=len(SERIES)) as pool:
            jobs = {pool.submit(fetch, key, today, entries[key]): key for key in SERIES}
            for job in as_completed(jobs):
                key = jobs[job]
                try:
                    frame = job.result()
                    entries[key]["validation"] = validate(
                        key, frame, previous.loc[previous["indicator"].eq(key)], today)
                    frames[key] = frame.assign(indicator=key)
                except Exception as exc:
                    entries[key]["validation"] = "Failed"
                    entries[key]["error"] = str(exc)
        status = "success" if len(frames) == len(SERIES) else "failed"
        if status == "success":
            # Store the CSV as immutable content, then commit the pointer and status together.
            combined = pd.concat([frames[key] for key in SERIES], ignore_index=True)
            csv_text = combined[["indicator", "date", "value"]].to_csv(index=False, date_format="%Y-%m-%d")
            import hashlib
            digest = hashlib.sha256(csv_text.encode()).hexdigest()[:20]
            snapshot = f"data/snapshots/{digest}.csv"
            path = root / snapshot
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
                temp = Path(file.name)
                file.write(csv_text)
            temp.replace(path)
            state["snapshot"] = snapshot
        completed = datetime.now(BRT)
        log = {
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "timezone": BRT.key,
            "status": status,
            "indicators": entries,
            "active_snapshot": state.get("snapshot"),
        }
        log_path = f"verification/refresh_{started:%Y%m%dT%H%M%S}_{uuid4().hex[:8]}.json"
        atomic_json(root / log_path, log)
        state["last_attempt"] = log
        state["last_attempt_log"] = log_path
        if status == "success":
            state["last_successful_refresh"] = log["completed_at"]
        atomic_json(directory / "state.json", state)
        return log


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    result = refresh(parser.parse_args().root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(result["status"] != "success")
