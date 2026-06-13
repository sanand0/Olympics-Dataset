"""Update athlete bios for athletes in the latest Olympic results."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys

from bs4 import BeautifulSoup
import pandas as pd

from update_results import BASE_URL, EDITIONS, get


CACHE_DIR = Path(".cache/olympedia/athletes")
RAW_COLUMNS = [
    "Roles",
    "Sex",
    "Full name",
    "Used name",
    "Born",
    "Died",
    "NOC",
    "athlete_id",
    "Measurements",
    "Affiliations",
    "Nick/petnames",
    "Title(s)",
    "Other names",
    "Nationality",
    "Original name",
    "Name order",
]
CLEAN_COLUMNS = [
    "athlete_id",
    "name",
    "born_date",
    "born_city",
    "born_region",
    "born_country",
    "NOC",
    "height_cm",
    "weight_kg",
    "died_date",
]
DATE_PATTERN = r"(\d+ \w+ \d{4}|\d{4})"
LOCATION_PATTERN = r"in ([\w\s()-]+), ([\w\s-]+) \((\w+)\)"


def target_athlete_ids() -> list[int]:
    """Return all athlete IDs from the latest Games."""
    results = pd.read_csv(
        "results/results.csv", usecols=["Games", "athlete_id"], low_memory=False
    )
    latest = results.loc[results["Games"].isin(EDITIONS.values()), "athlete_id"]
    return sorted(set(latest))


def fallback_bios(missing_ids: list[int]) -> pd.DataFrame:
    """Build basic bios from authoritative result names and NOCs."""
    results = pd.read_csv(
        "results/results.csv",
        usecols=["Games", "Event", "As", "athlete_id", "NOC"],
        low_memory=False,
    )
    results = results.loc[
        results["Games"].isin(EDITIONS.values()) & results["athlete_id"].isin(missing_ids)
    ]
    noc_data = pd.read_csv("clean-data/noc_regions.csv", keep_default_na=False)
    noc_data["name"] = noc_data["notes"].where(noc_data["notes"].ne(""), noc_data["region"])
    noc_regions = noc_data.set_index("NOC")["name"].to_dict() | {
        "AIN": "Individual Neutral Athletes",
        "EOR": "Refugee Olympic Team",
        "LBN": "Lebanon",
        "SGP": "Singapore",
    }

    output = []
    for athlete_id, rows in results.groupby("athlete_id", sort=True):
        events = " ".join(rows["Event"].dropna())
        sexes = {
            sex
            for label, sex in [(", Men", "Male"), (", Women", "Female")]
            if label in events
        }
        name = rows["As"].dropna().iloc[0]
        noc = rows["NOC"].dropna().iloc[0]
        values = {
            "Roles": "Competed in Olympic Games",
            "Sex": sexes.pop() if len(sexes) == 1 else "",
            "Full name": name,
            "Used name": name,
            "NOC": noc_regions.get(noc, noc),
            "athlete_id": athlete_id,
        }
        output.append({column: values.get(column, "") for column in RAW_COLUMNS})
    return pd.DataFrame(output, columns=RAW_COLUMNS)


def download_athlete(athlete_id: int) -> int:
    """Download one athlete page and return its ID."""
    get(
        f"{BASE_URL}/athletes/{athlete_id}",
        CACHE_DIR / f"{athlete_id}.html",
    )
    return athlete_id


def parse_athlete(athlete_id: int) -> dict:
    """Parse one athlete page into the repository's raw bio schema."""
    path = CACHE_DIR / f"{athlete_id}.html"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    table = soup.select_one("table.biodata")
    if not table:
        raise ValueError(f"No biodata table for athlete {athlete_id}")

    values = {
        row.th.get_text(strip=True): row.td.get_text(" ", strip=True)
        for row in table.select("tr")
    }
    return {column: athlete_id if column == "athlete_id" else values.get(column, "") for column in RAW_COLUMNS}


def clean_bios(raw: pd.DataFrame) -> pd.DataFrame:
    """Build analysis-friendly bios using the notebook's existing rules."""
    measurements = raw["Measurements"].str.split("/", n=1, expand=True)
    height = pd.to_numeric(measurements[0].str.strip(" cm"), errors="coerce")
    weight = (
        pd.to_numeric(measurements[1].str.strip(" kg"), errors="coerce")
        if measurements.shape[1] > 1
        else pd.Series(index=raw.index, dtype=float)
    )
    locations = raw["Born"].str.extract(LOCATION_PATTERN, expand=True)
    return pd.DataFrame(
        {
            "athlete_id": raw["athlete_id"],
            "name": raw["Used name"].str.replace("•", " ", regex=False),
            "born_date": pd.to_datetime(
                raw["Born"].str.extract(DATE_PATTERN, expand=False),
                format="mixed",
                errors="coerce",
            ),
            "born_city": locations[0],
            "born_region": locations[1],
            "born_country": locations[2],
            "NOC": raw["NOC"],
            "height_cm": height,
            "weight_kg": weight,
            "died_date": pd.to_datetime(
                raw["Died"].str.extract(DATE_PATTERN, expand=False),
                format="mixed",
                errors="coerce",
            ),
        },
        columns=CLEAN_COLUMNS,
    )


def main(download: bool = False) -> None:
    target_ids = target_athlete_ids()
    raw = pd.read_csv("athletes/bios.csv", low_memory=False)
    missing_ids = sorted(set(target_ids) - set(raw["athlete_id"]))
    print(f"Building {len(missing_ids)} missing athlete bios from results")
    if download:
        target = raw.loc[raw["athlete_id"].isin(target_ids)]
        partial = target[
            target[["Born", "Died", "Measurements", "Affiliations"]]
            .fillna("")
            .eq("")
            .all(axis=1)
        ]
        download_ids = sorted(set(missing_ids) | set(partial["athlete_id"]))
        print("Downloading full Olympedia biodata; this is source-rate-limited")
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(download_athlete, athlete_id)
                for athlete_id in download_ids
            ]
            for index, future in enumerate(as_completed(futures), 1):
                future.result()
                if index % 100 == 0 or index == len(download_ids):
                    print(f"Downloaded {index}/{len(download_ids)}")

    cached_ids = [
        athlete_id
        for athlete_id in target_ids
        if (CACHE_DIR / f"{athlete_id}.html").exists()
    ]
    fallback = fallback_bios(target_ids).set_index("athlete_id")
    existing = raw.loc[raw["athlete_id"].isin(target_ids)].set_index("athlete_id")
    original = existing.copy()
    for column in ["Roles", "Sex", "Full name", "Used name", "NOC"]:
        missing = existing[column].fillna("").eq("")
        existing.loc[missing, column] = fallback.loc[existing.index[missing], column]
    changed_ids = existing.index[existing.fillna("").ne(original.fillna("")).any(axis=1)]

    updates = pd.concat([fallback.loc[missing_ids], existing.loc[changed_ids]])
    enriched = pd.DataFrame(
        [parse_athlete(athlete_id) for athlete_id in cached_ids],
        columns=RAW_COLUMNS,
    ).set_index("athlete_id")
    for column in ["Roles", "Sex", "Full name", "Used name", "NOC"]:
        missing = enriched[column].fillna("").eq("")
        enriched.loc[missing, column] = fallback.loc[enriched.index[missing], column]
    updates = pd.concat([updates, enriched])
    updates = updates.loc[~updates.index.duplicated(keep="last")].reset_index()[RAW_COLUMNS]
    print(f"Enriched {len(cached_ids)} bios from cached Olympedia pages")
    if updates["athlete_id"].duplicated().any():
        raise ValueError("Duplicate athlete IDs in parsed bios")

    update_ids = set(updates["athlete_id"])
    raw = pd.concat([raw.loc[~raw["athlete_id"].isin(update_ids)], updates], ignore_index=True)
    raw.to_csv("athletes/bios.csv", index=False)

    clean_latest = clean_bios(updates)
    clean = pd.read_csv("clean-data/bios.csv", low_memory=False)
    clean = pd.concat(
        [clean.loc[~clean["athlete_id"].isin(update_ids)], clean_latest],
        ignore_index=True,
    )
    clean.to_csv("clean-data/bios.csv", index=False)

    locs = pd.read_csv("clean-data/bios_locs.csv", low_memory=False)
    locs_latest = clean_latest.assign(lat=pd.NA, long=pd.NA)
    locs = pd.concat(
        [locs.loc[~locs["athlete_id"].isin(update_ids)], locs_latest],
        ignore_index=True,
    )
    locs.to_csv("clean-data/bios_locs.csv", index=False)
    print(f"Wrote {len(raw)} raw, {len(clean)} clean, and {len(locs)} located bio rows")


if __name__ == "__main__":
    main(download="--download" in sys.argv)
