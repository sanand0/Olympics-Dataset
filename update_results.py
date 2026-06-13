"""Update Olympic results from Olympedia's edition result pages."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import time

from bs4 import BeautifulSoup
import pandas as pd
import requests


BASE_URL = "https://www.olympedia.org"
CACHE_DIR = Path(".cache/olympedia/results")
EDITIONS = {
    63: "2024 Summer Olympics",
    72: "2026 Winter Olympics",
}
RAW_COLUMNS = [
    "Games",
    "Event",
    "Team",
    "Pos",
    "Medal",
    "As",
    "athlete_id",
    "NOC",
    "Discipline",
    "Nationality",
    "Unnamed: 7",
]
DISCIPLINES = {
    "3x3 Basketball": "3x3 Basketball (Basketball)",
    "Alpine Skiing": "Alpine Skiing (Skiing)",
    "Artistic Gymnastics": "Artistic Gymnastics (Gymnastics)",
    "Artistic Swimming": "Artistic Swimming (Aquatics)",
    "Beach Volleyball": "Beach Volleyball (Volleyball)",
    "Bobsleigh": "Bobsleigh (Bobsleigh)",
    "Canoe Slalom": "Canoe Slalom (Canoeing)",
    "Canoe Sprint": "Canoe Sprint (Canoeing)",
    "Cross Country Skiing": "Cross Country Skiing (Skiing)",
    "Cycling BMX Freestyle": "Cycling BMX Freestyle (Cycling)",
    "Cycling BMX Racing": "Cycling BMX Racing (Cycling)",
    "Cycling Mountain Bike": "Cycling Mountain Bike (Cycling)",
    "Cycling Road": "Cycling Road (Cycling)",
    "Cycling Track": "Cycling Track (Cycling)",
    "Diving": "Diving (Aquatics)",
    "Equestrian Dressage": "Equestrian Dressage (Equestrian)",
    "Equestrian Eventing": "Equestrian Eventing (Equestrian)",
    "Equestrian Jumping": "Equestrian Jumping (Equestrian)",
    "Figure Skating": "Figure Skating (Skating)",
    "Football": "Football (Football)",
    "Freestyle Skiing": "Freestyle Skiing (Skiing)",
    "Ice Hockey": "Ice Hockey (Ice Hockey)",
    "Marathon Swimming": "Marathon Swimming (Aquatics)",
    "Nordic Combined": "Nordic Combined (Skiing)",
    "Rhythmic Gymnastics": "Rhythmic Gymnastics (Gymnastics)",
    "Rugby Sevens": "Rugby Sevens (Rugby)",
    "Short Track Speed Skating": "Short Track Speed Skating (Skating)",
    "Skateboarding": "Skateboarding (Roller Sports)",
    "Skeleton": "Skeleton (Bobsleigh)",
    "Ski Jumping": "Ski Jumping (Skiing)",
    "Snowboarding": "Snowboarding (Skiing)",
    "Speed Skating": "Speed Skating (Skating)",
    "Swimming": "Swimming (Aquatics)",
    "Trampolining": "Trampolining (Gymnastics)",
    "Volleyball": "Volleyball (Volleyball)",
    "Water Polo": "Water Polo (Aquatics)",
}


def get(url: str, path: Path | None = None) -> str:
    """Fetch a page, using a local checkpoint when a path is supplied."""
    if path and path.exists():
        return path.read_text(encoding="utf-8")

    for attempt in range(10):
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(response.text, encoding="utf-8")
            return response.text
        except requests.RequestException as error:
            if attempt == 9:
                raise
            retry_after = error.response.headers.get("Retry-After") if error.response else None
            rate_limited = error.response is not None and error.response.status_code == 429
            time.sleep(float(retry_after or (60 * (attempt + 1) if rate_limited else 2**attempt)))
    raise RuntimeError("unreachable")


def edition_events(edition_id: int) -> list[tuple[int, str, str]]:
    """Return event IDs, names, and disciplines for an edition."""
    soup = BeautifulSoup(
        get(f"{BASE_URL}/editions/{edition_id}/result"), "html.parser"
    )
    events = []
    discipline = ""
    for row in soup.select("table.table tr"):
        heading = row.select_one("h2")
        if heading:
            discipline = DISCIPLINES.get(heading.get_text(strip=True), heading.get_text(strip=True))
            continue
        link = row.select_one('a[href^="/results/"]')
        if link:
            events.append(
                (int(link["href"].rsplit("/", 1)[1]), link.get_text(strip=True), discipline)
            )
    return events


def download_event(event: tuple[int, str, str]) -> tuple[int, str, str]:
    """Download one event page and return its metadata."""
    event_id, _, _ = event
    get(f"{BASE_URL}/results/{event_id}", CACHE_DIR / f"{event_id}.html")
    return event


def cell_value(row, header: str) -> str:
    """Return a table cell value by header name."""
    headers = row.find_parent("table").select_one("thead").select("th")
    index = next((i for i, item in enumerate(headers) if item.get_text(strip=True) == header), None)
    cells = row.select("td")
    return cells[index].get_text(" ", strip=True) if index is not None and index < len(cells) else ""


def medal_value(row) -> str:
    """Return the medal encoded by a result row."""
    medal = row.select_one(".Gold, .Silver, .Bronze")
    return medal.get_text(strip=True) if medal else ""


def parse_event(event: tuple[int, str, str], games: str) -> list[dict]:
    """Parse one event page into the repository's athlete-result rows."""
    event_id, event_name, discipline = event
    soup = BeautifulSoup((CACHE_DIR / f"{event_id}.html").read_text(encoding="utf-8"), "html.parser")
    table = soup.select_one("table.table-striped")
    if not table:
        raise ValueError(f"No result table for {event_id}: {event_name}")

    output = []
    team = ""
    team_noc = ""
    team_pos = ""
    team_medal = ""
    for row in table.select("tr"):
        athletes = row.select('a[href^="/athletes/"]')
        noc_link = row.select_one('a[href^="/countries/"]')
        noc = noc_link["href"].rsplit("/", 1)[1] if noc_link else ""
        pos = cell_value(row, "Pos")
        medal = medal_value(row)

        if not athletes:
            if noc:
                team = (
                    cell_value(row, "Team")
                    or cell_value(row, "Competitor")
                    or cell_value(row, "Competitors")
                )
                team_noc, team_pos, team_medal = noc, pos, medal
            continue

        is_team_member = not noc and bool(team_noc)
        row_team = team if is_team_member else ""
        row_noc = team_noc if is_team_member else noc
        row_pos = team_pos if is_team_member else pos
        row_medal = team_medal if is_team_member else medal
        names = [athlete.get_text(" ", strip=True) for athlete in athletes]
        for athlete, name in zip(athletes, names):
            partner = next((other for other in names if other != name), "")
            output.append(
                {
                    "Games": games,
                    "Event": f"{event_name} (Olympic)",
                    "Team": row_team or partner,
                    "Pos": row_pos,
                    "Medal": row_medal,
                    "As": name,
                    "athlete_id": int(athlete["href"].rsplit("/", 1)[1]),
                    "NOC": row_noc,
                    "Discipline": discipline,
                    "Nationality": "",
                    "Unnamed: 7": "",
                }
            )
    return output


def clean_results(raw: pd.DataFrame) -> pd.DataFrame:
    """Build the analysis-friendly result table used in clean-data."""
    clean = pd.DataFrame(
        {
            "year": pd.to_numeric(
                raw["Games"].str.extract(r"(\d{4})", expand=False)
            ).astype(float),
            "type": raw["Games"].str.extract(r"(Summer|Winter)", expand=False),
            "discipline": raw["Discipline"],
            "event": raw["Event"],
            "as": raw["As"],
            "athlete_id": raw["athlete_id"],
            "noc": raw["NOC"],
            "team": raw["Team"],
            "place": pd.to_numeric(
                raw["Pos"].astype("string").str.extract(r"(\d+)", expand=False)
            ).astype(float),
            "tied": raw["Pos"].astype("string").str.contains("=", na=False),
            "medal": raw["Medal"],
        }
    )
    return clean


def main() -> None:
    all_rows = []
    for edition_id, games in EDITIONS.items():
        events = edition_events(edition_id)
        print(f"{games}: downloading {len(events)} events")
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(download_event, event) for event in events]
            for index, future in enumerate(as_completed(futures), 1):
                future.result()
                if index % 50 == 0 or index == len(events):
                    print(f"{games}: downloaded {index}/{len(events)}")

        rows = [row for event in events for row in parse_event(event, games)]
        print(f"{games}: parsed {len(rows)} athlete-event results")
        all_rows.extend(rows)

    latest = pd.DataFrame(all_rows, columns=RAW_COLUMNS)
    duplicate_keys = ["Games", "Event", "athlete_id"]
    duplicates = latest.duplicated(duplicate_keys, keep=False)
    if duplicates.any():
        examples = latest.loc[duplicates, duplicate_keys].head().to_dict("records")
        raise ValueError(f"Duplicate athlete-event rows: {examples}")

    raw = pd.read_csv("results/results.csv", low_memory=False)
    raw = raw.loc[~raw["Games"].isin(EDITIONS.values()), RAW_COLUMNS]
    raw = pd.concat([raw, latest], ignore_index=True)
    raw.to_csv("results/results.csv", index=False)

    clean = pd.read_csv("clean-data/results.csv", low_memory=False)
    latest_years = {int(games[:4]) for games in EDITIONS.values()}
    clean = clean.loc[~clean["year"].isin(latest_years)]
    clean = pd.concat([clean, clean_results(latest)], ignore_index=True)
    clean.to_csv("clean-data/results.csv", index=False)
    print(f"Wrote {len(raw)} raw rows and {len(raw)} clean rows")


if __name__ == "__main__":
    main()
