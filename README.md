# tellway-data

Offline OpenStreetMap packages for the TellWay app, one per country.

- `catalog.json` — list of available packages (the app reads this)
- Releases `<country>-<YYYYMMDD>` — the `.twpack` files

Format and build script: `tool/osm_pack/` in the TellWay source
(`measure.py` → `build_pack.py`).

Data © OpenStreetMap contributors, ODbL 1.0 — see `LICENSE.md`.
