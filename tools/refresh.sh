#!/bin/sh
# Rebuild the KSFO data package from fresh downloads.
#   tools/refresh.sh [CIFP_ZIP_URL]
# Without an argument the current cycle's zip URL is taken from the FAA CIFP page.
# Downloads go into a new, empty raw/ directory and are only read as data by the
# builders (python3 -I, scripts live in tools/, outside raw/).
set -eu
cd "$(dirname "$0")/.."
ROOT=$(pwd)
PAGE=https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/download/
URL=${1:-$(curl -sL "$PAGE" | grep -oE 'https://aeronav\.faa\.gov/[^"]*/cifp/CIFP_[0-9]+\.zip' | head -1)}
[ -n "$URL" ] || { echo "could not find a CIFP zip link on $PAGE" >&2; exit 1; }
STAMP=$(date +%Y%m%d-%H%M%S)
RAW="$ROOT/raw/$STAMP"
mkdir -p "$RAW/cifp" "$RAW/osm" "$RAW/mva"
echo "CIFP: $URL"
curl -sSfL -o "$RAW/cifp.zip" "$URL"
(cd "$RAW/cifp" && unzip -q ../cifp.zip)
echo "MVA: NCT FUS3/FUS5 AIXM"
curl -sSfL -o "$RAW/mva/NCT_MVA_FUS3.xml" https://aeronav.faa.gov/MVA_Charts/aixm/NCT_MVA_FUS3.xml
curl -sSfL -o "$RAW/mva/NCT_MVA_FUS5.xml" https://aeronav.faa.gov/MVA_Charts/aixm/NCT_MVA_FUS5.xml
echo "OSM: one Overpass query (retry by hand later if the server is busy)"
curl -sSf -A "if-ai-atc-ksfo-data-builder/0.1" --data-urlencode "data@$ROOT/tools/overpass_query.txt" \
  https://overpass-api.de/api/interpreter -o "$RAW/osm/ksfo_aeroway.json"
cd /
python3 -I "$ROOT/tools/build_cifp.py" "$RAW/cifp/FAACIFP18" "$ROOT/ksfo/data"
python3 -I "$ROOT/tools/build_osm.py" "$RAW/osm/ksfo_aeroway.json" "$ROOT/ksfo/data/airport.json" "$ROOT/ksfo/data"
python3 -I "$ROOT/tools/build_mva.py" "$ROOT/ksfo/data/airport.json" "$ROOT/ksfo/data" "$RAW/mva/NCT_MVA_FUS3.xml" "$RAW/mva/NCT_MVA_FUS5.xml"
python3 -I "$ROOT/tools/build_map.py" "$ROOT/ksfo/data" "$ROOT/ksfo/map.html"
python3 -I "$ROOT/tools/validate.py"
