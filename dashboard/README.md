# dashboard

The live dashboard at https://atc.kyleboas.com. This folder is what the server runs: deploys
pull it from GitHub, so change it here and push.

- `server.py`: the dashboard and sim replays (`python3 dashboard/server.py 8795`). Set
  `ATC_ROOT` to the research project it shows and `ATC_CACHE_DIR` to keep its cache outside
  the checkout. `ATC_MIRROR=1` serves a read-only copy of a project synced from elsewhere.
- `replays.py`: sim replays on the scope. `/replay?commit=&seed=` makes the replay as before
  (`atc/replay.html`, cached), then sends the browser to `/replay?tl=<commit>-<seed>`, which plays it on
  [scope](https://github.com/kyleboas/scope) (the same scope as livescope) with its timelapse bar and a
  radio log. The server runs scope in-process from a checkout beside this one (`ATC_SCOPE_DIR`, default
  `../scope`; its map caches, and each replay once converted for it, go in `ATC_SCOPE_CACHE`, default
  `~/.cache/atc-dashboard/scope`) and restarts
  itself when that checkout's commit changes (`ATC_SCOPE_RELOAD=0` turns that off). Without scope the old
  replay page is served.
- `index.html`: the page.
- `github_hook.py`: webhook for pull requests that pass both checks (see the file).

Contributions still change only `controller.py`; this folder is not scored.
