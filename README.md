# Olympics-Dataset

This repo contains a comprehensive dataset on summer & winter Olympic results and participants between 1896-2026. New participant bios always include names and NOCs; richer biodata depends on individual Olympedia page enrichment.

![Olympic Flame](./assets/olympic_flame.jpeg)

## Dataset info & collection process

This data comes from [olympedia.org](https://www.olympedia.org/) and was web scraped with the Python Beautiful Soup library (see [scrape_data.py](./scrape_data.py))

- [athletes/bios.csv](./athletes/bios.csv) contains the raw biographical information on each athlete<br/>
- [results/results.csv](./results/results.csv) contains a row-by-row breakdown of each event athletes competed in and their results in that event.
- [update_results.py](./update_results.py) updates the result files from Olympedia's edition result pages. Downloads are cached under `.cache/` so interrupted updates can resume.
- [update_bios.py](./update_bios.py) updates athlete bios for athletes found in the latest result editions. It fills names, NOCs, and inferable sex from result data; run with `--download` to resumably enrich all biodata from individual Olympedia pages. New locations are left ungeocoded in `clean-data/bios_locs.csv`.

Note, in the process of scraping this dataset, temporary CSV files were created to checkpoint scraping progress. For simplicity these checkpointed files have since been removed from the repository.

## Clean Data

Easier to analyze data can be found in [clean-data/](./clean-data/) folder. In addition to the results and bios info, you can find data files with additional lat/long location data for athletes, NOC region codes, and historic populations of countries over time.
