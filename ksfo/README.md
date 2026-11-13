# KSFO data package for the ATC simulator

Real-world data for San Francisco International (KSFO), built from public
sources, used by the simulator in this repository.
It contains runways, ILS, every SID, STAR and instrument approach coded in the
FAA CIFP (as ARINC 424 legs), the fixes and navaids they use, holding
patterns, NorCal TRACON minimum vectoring altitudes, and an OpenStreetMap
taxiway graph with stands and holding positions. `ksfo/map.html` is a
self-contained map for checking the data against the published charts.

> **NOT FOR REAL-WORLD NAVIGATION.** This is simulator data. It is derived from
> one AIRAC cycle, was parsed by hand-written code, and is not maintained.
> Procedures change every 28 days. Always use current official FAA
> publications for flight.

## Sources

| Data | Source | Version used |
|---|---|---|
| Procedures, runways, ILS, fixes, navaids, MSA | FAA **CIFP** (Coded Instrument Flight Procedures, ARINC 424-18), <https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/download/> | **AIRAC cycle 2611, effective 29 Oct 2026** (`CIFP_261029.zip`, file created 07-Oct-2026, sha256 `e3ff2b31…5e141d`) |
| Minimum vectoring altitudes | FAA AIS MVA charts, NCT (Northern California TRACON), AIXM 5.1: `NCT_MVA_FUS3.xml` and `NCT_MVA_FUS5.xml`, <https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/mva_mia/mva/> | Chart description `NCT_MVA_FUS3_2025` / `NCT_MVA_FUS5_2025`, downloaded 2026-11-11 |
| Airport surface | OpenStreetMap through the Overpass API: one query for `aeroway=*` in bbox 37.595,-122.405,37.645,-122.350 (`tools/overpass_query.txt`) | OSM base timestamp 2026-11-11T17:38:27Z |

Raw downloads go in `raw/` (git-ignored). They are treated as untrusted data:
nothing in `raw/` is executed, the parsers live in `tools/`, and all Python runs
with `python3 -I`. Everything is Python standard library only.

## Licensing

* **FAA data** (CIFP, MVA charts) is a U.S. Government work and in the **public domain**. The FAA CIFP disclaimer applies: the data is provided as is, with no warranty.
* **OpenStreetMap data** (`ksfo/data/surface.json`, and the surface layer of `ksfo/map.html`) is **© OpenStreetMap contributors**, available under the **Open Database License (ODbL) 1.0**, <https://www.openstreetmap.org/copyright>. If you publicly use or redistribute the surface data or a database derived from it, you must credit "© OpenStreetMap contributors", say it is under the ODbL, and offer any derived database under the ODbL as well (share-alike). The map page shows this attribution.
* Code in this repository: same terms as the parent simulator project.

## Layout

```
ksfo/
  data.py          loader: world.py-style constants and helpers
  geo.py           WGS-84 Vincenty + local azimuthal-equidistant frame
  data/airport.json      ARP, elevation, mag var, runways, localizers/GS, FAS path points, MSA
  data/procedures.json   39 procedures -> transitions -> ARINC legs
  data/fixes.json        191 fixes/navaids/thresholds referenced (+ all KSFO terminal waypoints)
  data/holds.json        15 holding patterns (HM legs)
  data/star_holds.json   22 holds charted on the STARs (hand entered from the d-TPP charts)
  data/surface.json      OSM taxiway graph, stands, gates, holding points, aprons (ODbL)
  data/mva.json          NCT MVA sectors (FUS3 150 + FUS5 59) as polygons
  map.html         self-contained check map (data embedded, ~0.8 MB)
tools/
  build_cifp.py    ARINC 424 parser -> airport/procedures/fixes/holds.json
  build_osm.py     Overpass JSON -> surface.json (graph construction)
  build_mva.py     AIXM MVA -> mva.json
  build_map.py     data -> map.html (uses map_template.html)
  validate.py      spot checks against published values, CIFP vs OSM, projection
  refresh.sh       download everything for a new cycle and rebuild
  overpass_query.txt, xlsx_dump.py (reads the CIFP "Not in CIFP" list)
```

## Coordinates

* Everything keeps **lat/lon** (WGS-84; the CIFP datum is NAD83, which is within about 1–2 m of WGS-84 here).
* Local **x/y in nautical miles**, x east, y north, origin at the **KSFO ARP** (37°37'07.70"N, 122°22'31.50"W), the same orientation as `atc/world.py`.
* Projection: **azimuthal equidistant on the WGS-84 ellipsoid** (`ksfo/geo.py`): x = s·sin(az), y = s·cos(az), where s and az are the Vincenty geodesic distance and initial true azimuth from the ARP. Distance and bearing *from the ARP* are exact.
  * Distances between two other points are stretched by about ρ²/6 (ρ = s/3440 nm): 5×10⁻⁵ at 60 nm (≈0.003 nm). Measured: ARCHI–BDEGA (39.5 nm) 0 ft error; DYAMD–SERFR (108 nm, both ~100 nm out) 64 ft.
  * Grid north is true north **at the ARP only**. Away from the ARP the local true north is rotated by the meridian convergence (≈Δlon·sin lat): about 0.3° at 25 nm east/west, 0.8° at 60 nm, 1.2° on the DYAMD–SERFR leg ~100 nm out. To fly an exact true course far from the field, use lat/lon. The farthest referenced fix (RBG, an enroute transition start point) is 337 nm away.
* **Magnetic variation:** the CIFP airport record and the ILS station declinations use **14.0° E**. Runway magnetic bearings (284/104/014/194) are based on it; the true bearings computed from the threshold coordinates are 297.81/117.79/27.80/207.81, which gives an implied variation of 13.8°. The CIFP waypoint records near SFO carry a newer variation of **12.8° E** (12.4–13.1 across the area). Leg courses in procedures are magnetic unless `course_is_true` is set; `ksfo.data.mag_to_true()` adds 14.0.
* Altitudes are feet MSL (field elevation 13 ft). FL values are kept as `{"ft": 27000, "fl": 270}`. The transition altitude is 18,000 ft.

## Data model

### `airport.json`
`lat, lon, elevation_ft, magvar, transition_altitude_ft`, `source{cycle, effective}`, `projection`, and:

* `runways[]`: `ident` (01L…28R), `length_ft`, `width_ft`, `magnetic_bearing`, `true_bearing` (computed), `threshold_lat/lon/x/y` (the **landing threshold**, i.e. the displaced one where there is one), `displaced_threshold_ft`, `runway_end_lat/lon/x/y` (start of the pavement = threshold moved back by the displacement), `threshold_elevation_ft`, `tch_ft`, `ellipsoid_height_m`, `localizer`, `ils_category`, `opposite`, `threshold_to_threshold_ft`.
* `localizers[]`: `ident, freq_mhz, runway, ils_category, loc_lat/lon/x/y, gs_lat/lon/x/y, loc_magnetic_bearing, true_course, gs_angle_deg, gs_tch_ft, loc_width_deg, gs_elevation_ft, station_declination`.
* `fas_path_points[]`: SBAS FAS data blocks for the RNAV (GPS) LPV finals (LTP, FPAP, glide path angle, TCH).
* `msa[]`: minimum sector altitudes (KSFO and runway-centred 25 nm MSA 5,000/5,100 ft; SFO VOR sectors 3,500/4,500/5,000).

### `procedures.json`
`procedures[]`, each with `id` (CIFP id: `SERFR4`, `I28L`, `H28RY`…), `kind` (`SID`/`STAR`/`APPROACH`), for approaches `name` (`ILS RWY 28L`, `RNAV (RNP) Y RWY 28R`…), `approach_type`, `runway`, `variant`, `has_rf_legs`; for SIDs/STARs `runways`, `enroute_transitions`, `runway_transitions`; and `transitions[]`:

* `route_type` (ARINC code) and `route_type_name`, `transition` (e.g. `RW28B`, `LEGGS`, `ARCHI`, or null for a common route / final), `segment` (`runway_transition`, `common`, `enroute_transition`, approach `transition` or `final`), `runways` (`RW28B` → `["28L","28R"]`).
* `legs[]` in sequence order, with only the fields that apply:
  `seq`, `path_term` (ARINC leg type: IF TF CF DF RF CA VA VI VM FM HM …) and `path_term_name`; `fix`, `fix_ref` (key into fixes.json); `waypoint_desc` (raw 4 columns), `flyover`, `role` (IAF, IF, FAF, missed approach point, final approach course fix…), `missed_approach_start`, `missed` (every leg after the MAP); `turn` (left/right) and `turn_valid`; `rnp_nm`; `recommended_navaid` (+ref), `theta_deg`, `rho_nm`; `course_deg` + `course_is_true`; `distance_nm` or `hold_time_min`; `arc_radius_nm`, `arc_center` (+ref) on RF legs; `altitude` = `{code, meaning, alt1, alt2, type: at|at_or_above|at_or_below|between, min_ft, max_ft, glideslope_ft?}` and `altitude_text` (`"7000..9000"`, `"5000+"`, `"FL270+"`, `"10000-"`); `speed_kt` + `speed_type` (`at` / `at or below` / `at or above`); `vertical_angle_deg`; `transition_altitude_ft`.

ARINC altitude codes kept as published: `+` at or above, `-` at or below, blank/`@` at, `B` between (alt1 = upper, alt2 = lower), `J` at or above with a glideslope intercept altitude, `H` at or above with a glideslope altitude at the FAF, `V` step-down fix with the vertical-path altitude in alt2. Speed description: blank = at (mandatory, ARINC 424 rule), `-` = at or below. Check the chart where it matters.

### `fixes.json`
`fixes{"IDENT/SECTION": {...}}` with `ident, kind` (terminal waypoint, enroute waypoint, VHF navaid, localizer, runway threshold, airport), `section` (ARINC section: `PC`, `EA`, `D`, `PI`, `PG`, `PA`), `lat, lon, x, y`, `magvar`, `type_code`, `name`, and for navaids `freq_mhz`, `class`, `dme_lat/lon`. The key includes the section because one ident can exist as both a navaid and a waypoint.

### `holds.json`
`holds[]`: `fix, procedure, leg_type, inbound_course_deg` (magnetic), `turn`, `leg_nm` or `leg_min`, `altitude`, `speed_kt`. All are missed-approach holds (HM). No enroute holding records (`EP`) exist in this CIFP.

### `star_holds.json`
The holds drawn on the STAR charts, which the CIFP does not code. They are hand-entered from the d-TPP cycle 2611 charts and use the same fields as `holds.json`, with `kind: "STAR"`, `procedure` set to the STAR and `leg_type: "charted"`.

| STAR | Fix | Inbound (mag) | Turns | Legs |
|---|---|---|---|---|
| SERFR4, WWAVS2 | SERFR, NRRLI | 314° | left | 10 NM |
| SERFR4, WWAVS2 | WWAVS | 313° | left | 10 NM |
| SERFR4 | EPICK | 333° | left | 10 NM |
| WWAVS2 | WPOUT | 306° | left | 10 NM |
| DYAMD5, ALWYS3 | INYOE | 248° | left | 16 NM |
| DYAMD5 | ALWYS, FLOWZ | 247° | left | 10 NM |
| ALWYS3 | LAANE | 247° | left | 10 NM |
| ALWYS3 | ALWYS | 269° | left | 10 NM |
| BDEGA4, STLER4 | MLBEC, BGGLO, LOZIT | 153° | right | 10 NM |
| BDEGA4, STLER4 | AMAKR | 121° | right | 10 NM |

YOSEM3 charts no holds. LOZIT is limited to 9000–16000 (its FAA charted-holding record). `data.HOLDS` holds both files, and `data.published_hold(fix, star)` prefers the hold charted on the aircraft's STAR. ALWYS is held inbound 247° on the DYAMD5 and 269° on the ALWYS3. A "hold as published" at a fix with a published hold flies that hold's course, turns and legs. At any other fix, the controller's turns are used with timed legs.

### `surface.json` (OSM, ODbL)
* `nodes{id: {x, y, lat, lon, osm_node?, holding_position?, stand?, gate_ref?, edges[], degree, main_component}}`. Ids: `n<osm id>`, `s<k>` (where an OSM holding-position line crosses a taxiway), `j<k>` (where an isolated stand line was joined to a taxiway).
* `edges[]`: `{a, b, name, kind, length_nm, length_ft, osm_way, geometry[[x,y]…]}`. `kind` is `taxiway`, `taxilane`, `parking_position` (stand lead-in line, named `stand <ref>`), `runway` / `runway_displaced` (centre lines, named `RWY 10L/28R`, so runway exits join the graph), or `stand_link` (generated).
* `stands[]` (`ref, node, entry_node, x, y, lat, lon, connected, snapped_link_ft`), `gates[]` (OSM gate nodes), `hold_lines[]`, `aprons[]`, `terminals[]`, `runways_osm[]`, `tower` (x, y), `taxiway_names`, `stats`.

How the graph is built: OSM taxiway, taxilane, parking-position and runway ways become edges. Nodes are created at way ends, at OSM nodes shared by two or more ways, at holding-position nodes, and where a holding-position line crosses a taxiway. An edge is the polyline between consecutive graph nodes along one way. Isolated stand lead-in lines (71 in this extract) are joined to the nearest taxiway or taxilane within 300 ft by a `stand_link` edge.

### `mva.json`
`charts[]` (FUS3, FUS5) → `sectors[]`: `name` (e.g. `OAK_C-1`), `mva_ft`, `exterior_xy` / `exterior_lonlat`, `holes_xy` / `holes_lonlat`. FUS3 is the chart used with 3 nm radar separation (the terminal area); FUS5 is used with 5 nm.

### Loader: `ksfo/data.py`
```python
import sys; sys.path.insert(0, "/path/to/if-ai-atc-ksfo")
from ksfo import data as K
K.RUNWAYS["28L"]        # threshold (x,y), runway_end, true/magnetic heading, length, elevation, displaced, TCH, ILS
K.ILS["28L"]            # ISFO 109.55, GS 2.85°, antenna positions, course
K.FIXES["DUYET"]        # (x, y)        K.FIX_RECORDS["DUYET/PC"] -> full record
K.STARS["BDEGA4"]       # {transition: [(fix, (min_ft, max_ft)), ...]}   (like world.STARS)
K.SIDS["SSTIK5"]
K.star_route("BDEGA4", "LEGGS", "28R")   # enroute + common + runway transition, full leg dicts
K.sid_route("SSTIK5", "01L", "YYUNG")
K.approach_route("H28RY", "ARCHI")       # transition + final + missed
K.approach_fixes("I28L")  # {'IAF': [...], 'IF': ['HEMAN'], 'FAF': 'DUYET', 'MAP': 'RW28L', 'FAF_ALT': 1800}
K.HOLDS
K.TAXI_NODES, K.TAXI_EDGES, K.TAXIWAYS, K.STANDS, K.HOLD_POINTS   # like world.TAXI_*
K.mva(x, y)               # FUS3 sector, else FUS5, else the 25 nm MSA (5,100 ft)
K.dist, K.bearing, K.mag_to_true, K.true_to_mag
```

## Map

Open `ksfo/map.html` in a browser (no network needed). Mouse wheel zooms and dragging pans. Layers can be toggled: MVA (FUS3/FUS5), aprons, runways, ILS courses, taxiway graph, taxiway names, nodes, holding positions, stands, gates, fixes, navaids, range rings and constraint labels. Tick any SID, STAR or approach to draw it, and untick single transitions to hide them. Constraints are labelled at each fix, and hovering a fix shows its coordinates and every selected leg that uses it. The side panel lists the selected legs in full. RF legs are drawn as true arcs about their centre fix. Legs without a terminating fix (VA, CA, VI, VM, FM) are dashed stubs along the coded course. Missed approaches are dashed, and holds are drawn as racetracks. The URL hash preselects a view, for example `map.html#p=SERFR4,I28L&s=9&c=0,0&l=mva3` (procedures, px per nm, centre in nm, layers).

## Validation (cycle 2611), `python3 -I tools/validate.py`: 0 failed checks

* **Runways:** 10L/28R 11,870 × 200 ft, 10R/28L 11,381 × 200, 01R/19L 8,650 × 200, 01L/19R 7,650 × 200. Magnetic 104/284/014/194, true 117.8/297.8/27.8/207.8. Displaced thresholds: 28L 300 ft, 28R 300 ft, 01R 560 ft, 01L 640 ft; the others have none. These all match the airport diagram.
* **ILS:** 28L I-SFO 109.55, **GS 2.85°**, TCH 53 ft, CAT II. 28R I-GWQ 111.70, GS 3.00°, TCH 55 ft, CAT III. 19L I-SIA 108.90, GS 3.00°, TCH 55 ft, CAT I.
* **ARP and elevation:** 37-37-07.70N 122-22-31.50W, 13 ft.
* **Navaids:** SFO VOR/DME 115.8 is 0.08 nm from the ARP. OSI 113.9 and SAU 116.2 are present. The reference coordinates in `validate.py` are hand-entered, so treat this as a consistency check.
* **CIFP vs OSM:** every CIFP runway pavement end is within 1–35 ft of an OSM runway way end. This independently confirms the thresholds, the displacements and the projection.
* **SIDs (11):** CIITY3, GAPP7 (vector SID), GNNRR3, MOLEN9, NIITE4, SAHEY4, SEGUL1, SNTNA2, SSTIK5, TRUKN2, WESLA5.
* **STARs (13):** ALWYS3, BDEGA4, BSR3, DYAMD5, MOD9, PIRAT3, PYE3, RISTI1, SERFR4, STINS4, STLER4, WWAVS2, YOSEM3. Transition lists are printed by `validate.py`; for example BDEGA4 has AMAKR/LEGGS/MLBEC/MRRLO plus RW01R/RW28B.
* **Approaches (15):** ILS 19L/28L/28R; LOC 19L/28L/28R; RNAV (GPS) 10L, Y 10R, 19L, Y 19R, Z 19R, 28L, Z 28R; RNAV (RNP) Z 10R, Y 28R.
* **RF legs:** RNAV (RNP) Y 28R has two RF legs, to JOSUF (left, r 2.92 nm, centre CFFJZ) and to FABLA (right, r 2.83 nm, centre CFFJX). The coded radius matches the centre-to-fix distance to 0.001 nm. RNAV (RNP) Z 10R has no RF legs in the CIFP.
* **Approach fixes:** ILS 28L uses IF HEMAN, FAF DUYET (1,800), MAP RW28L, missed approach to OLYMM with a hold. ILS 28R uses IF CEPIN, FAF AXMUL, missed approach to VIKYU with a hold. All 15 approaches have missed-approach legs, and all 191 referenced fixes resolve to a position.
* **Taxi graph:** 1,232 nodes and 1,603 edges; 1,230 nodes are in one connected component.

## Gaps and uncertainties

* **Not in CIFP** (from the FAA list `Not_In_CIFP_2611.xlsx`): GLS approaches J19L, J19R, J28L, J28R; `R28RU` (an RNAV (GPS) 28R variant); and the **SFO5 (SFO FIVE) departure**.
* **Charted visual procedures are not coded in CIFP**: Quiet Bridge Visual 28L/28R, Tipp Toe Visual 28L/28R and the FMS Bridge Visual 28R. They would have to be hand-built from the charts.
* There are **no LDA/PRM procedures** in this cycle's CIFP. PRM/SOIA operations (the old LDA PRM 28R) are not present.
* Conventional STARs (PYE3, STINS4, MOD9, BSR3, RISTI1 in part) carry few or no altitude constraints in CIFP. Charted "expect" altitudes are not coded.
* Several STARs end in FM/VM legs (vectors). Some STARs (MOD9, PIRAT3, PYE3, STINS4, RISTI1) have no runway transitions.
* A blank speed description is read as "at" (ARINC rule). FAA charts sometimes intend "maximum". Verify the speeds that matter on the chart.
* Holding legs keep the coded inbound course and leg length/time. The map's racetracks use an assumed 1.06 nm turn radius.
* **OSM surface:** 284 of 1,075 taxiway edges have no `ref` (unnamed connectors and high-speed exits). 171 of 289 parking positions have no ref. Gate refs are on 121 separate OSM gate nodes. 71 stand lines were joined to the graph by generated `stand_link` edges, and one stand is still disconnected (more than 300 ft from a taxiway). Taxiway names follow OSM and may lag the current airport diagram; check names such as `UAL`, `Plot 40 Line` and the numbered ramp lines.
* **MVA:** the AIXM files are titled `…_2025`. The FAA publishes MVA charts on their own schedule, not the AIRAC cycle. Outside both charts `mva()` falls back to the 25 nm MSA. The MVA over the field is 2,600 ft (sector OAK_C-1).
* The projection is exact from the ARP. See *Coordinates* for the convergence and scale caveats.

## Refreshing for a new AIRAC cycle

```
tools/refresh.sh                 # finds the current CIFP zip on the FAA page
tools/refresh.sh https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_261126.zip   # or name one (cycle 2612)
```
This downloads the CIFP zip, the NCT MVA AIXM files and one Overpass extract into a new timestamped `raw/` directory. It then runs `build_cifp → build_osm → build_mva → build_map → validate`. Review the `validate.py` output and the procedure list. Its expected sets (SID/STAR names, runway values) are hand-entered, so update them when procedures are amended (for example SERFR4 → SERFR5). If Overpass is busy, retry later with backoff; it is one query and only needs to be re-run when the airport changes. Check `Not_In_CIFP_*.xlsx` with `python3 -I tools/xlsx_dump.py <file> SFO`.
