#!/usr/bin/env bash
# Install the dashboard (atc.kyleboas.com) as a sandboxed system service, plus atc-update.timer, which pulls this
# checkout every minute and restarts the dashboard and the webhook when dashboard/ changed, so pushing to GitHub
# deploys. Run on coding:
#
#   sudo bash deploy/install-service.sh
#
# The dashboard runs as its own throwaway user (DynamicUser): this checkout, scope's and ~/atc-mirror (synced from
# the Mac) read-only, the rest of the home directory hidden, a writable cache in /var/lib/atc-dashboard, the internet
# only. Its settings (ATC_BATCH and so on) are in ~/.config/atc-dashboard.env, made from the old user unit; the
# updater restarts the dashboard when that file changes too.
#
# The webhook (atc-hook) stays a user unit: it files divernote issues as the agent user. It checks GitHub's signature
# before reading anything. This replaces gh-deploy's atc-at-home entry and the atc-dashboard user unit.
set -euo pipefail
command -v systemctl >/dev/null || { echo "This installs the site's Linux server (systemd). Run it there (ssh coding)." >&2; exit 1; }
[[ $EUID -eq 0 ]] || { echo "run it with sudo" >&2; exit 1; }

SITE="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8795}"
AGENT_USER="${SUDO_USER:-kyle}"
AGENT_HOME="$(getent passwd "$AGENT_USER" | cut -d: -f6)"
uid="$(id -u "$AGENT_USER")"
SCOPE="${SCOPE:-$AGENT_HOME/scope}"
MIRROR="${MIRROR:-$AGENT_HOME/atc-mirror}"
ENVF="$AGENT_HOME/.config/atc-dashboard.env"
OLD="$AGENT_HOME/.config/systemd/user/atc-dashboard.service"
as_agent() { sudo -u "$AGENT_USER" XDG_RUNTIME_DIR="/run/user/$uid" "$@"; }
[[ -f "$SCOPE/server.py" ]] || { echo "no scope checkout at $SCOPE (set SCOPE=...)" >&2; exit 1; }
[[ -d "$MIRROR" ]] || { echo "no mirror at $MIRROR (set MIRROR=...)" >&2; exit 1; }

# the settings, from the old user unit's Environment= lines (once)
if [[ ! -f "$ENVF" ]]; then
  if [[ -f "$OLD" ]]; then
    sed -n 's/^Environment=//p' "$OLD" | sed 's/^"\(.*\)"$/\1/' > "$ENVF"
  else
    printf 'ATC_ROOT=%s\nATC_CACHE_DIR=%s\nATC_MIRROR=1\nATC_SHOW_PAST=0\n' \
      "$MIRROR/if-ai-atc-ksfo" "$MIRROR/if-ai-atc-full/dashboard/cache" > "$ENVF"
  fi
  chown "$AGENT_USER:" "$ENVF"
fi
grep -q '^ATC_MIRROR=1' "$ENVF" || { echo "$ENVF must set ATC_MIRROR=1: the sandbox's mirror is read-only" >&2; exit 1; }

# retire the user unit and gh-deploy's entry
if [[ -f "$OLD" ]]; then
  as_agent systemctl --user disable --now atc-dashboard.service || true
  rm -f "$OLD"
  as_agent systemctl --user daemon-reload || true
fi
GHD="$AGENT_HOME/.local/bin/gh-deploy"
[[ -f "$GHD" ]] && sed -i '/^ *\[atc-at-home\]=/d' "$GHD"

cat > /etc/systemd/system/atc-dashboard.service <<UNIT
[Unit]
Description=ATC autoresearch dashboard (atc.kyleboas.com; read-only mirror, updated from the Mac)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
DynamicUser=true
EnvironmentFile=$ENVF
Environment=ATC_SCOPE_DIR=$SCOPE ATC_SCOPE_CACHE=/var/lib/atc-dashboard/scope
# scope_watch reads scope's HEAD: the checkout belongs to $AGENT_USER
Environment=GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=*
StateDirectory=atc-dashboard
WorkingDirectory=$SITE
ProtectHome=tmpfs
BindReadOnlyPaths=$SITE $SCOPE $MIRROR
ExecStart=/usr/bin/python3 $SITE/dashboard/server.py $PORT 127.0.0.1 --no-holdout
Restart=always
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
InaccessiblePaths=-/etc/agent-secrets
CapabilityBoundingSet=
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
# The internet only: not the droplet's private network, the tailnet, or the cloud metadata service. Loopback stays
# open (cloudflared reaches the site there, and DNS is 127.0.0.53), so services on 127.0.0.1 must check who's asking.
IPAddressDeny=169.254.0.0/16 fe80::/10 224.0.0.0/4 ff00::/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 fc00::/7
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged
SystemCallErrorNumber=EPERM
RestrictNamespaces=true
RestrictRealtime=true
ProtectHostname=true
ProtectProc=invisible
PrivateIPC=true
MemoryMax=1G

[Install]
WantedBy=multi-user.target
UNIT

UPDATER=/usr/local/lib/atc-update.sh
cat > "$UPDATER" <<SCRIPT
#!/usr/bin/env bash
# Fast-forward $SITE to origin/main (as $AGENT_USER, its owner); restart the dashboard and the webhook when
# dashboard/ changed, and the dashboard alone when $ENVF did. A checkout with local edits is skipped.
set -uo pipefail
st=/var/lib/atc-update
g() { runuser -u $AGENT_USER -- git -C $SITE "\$@"; }
g fetch -q origin main || { echo "fetch failed"; exit 0; }
old=\$(g rev-parse HEAD); new=\$(g rev-parse origin/main)
if [[ "\$old" != "\$new" ]]; then
  if [[ -n "\$(g status --porcelain --untracked-files=no)" ]]; then echo "local changes, not deploying \$new"; exit 0; fi
  g merge -q --ff-only origin/main || { echo "not a fast-forward, skipped"; exit 0; }
  echo "atc-at-home: \${old:0:7} -> \${new:0:7}"
fi
code="\$(g rev-parse HEAD:dashboard)"; envh="\$(sha256sum $ENVF | cut -c1-64)"
if [[ "\$code" != "\$(cat \$st/code 2>/dev/null)" ]]; then
  systemctl restart atc-dashboard.service
  runuser -u $AGENT_USER -- env XDG_RUNTIME_DIR=/run/user/$uid systemctl --user restart atc-hook.service || true
  echo "\$code" > \$st/code; echo "\$envh" > \$st/env; echo "restarted for dashboard/ \${code:0:7}"
elif [[ "\$envh" != "\$(cat \$st/env 2>/dev/null)" ]]; then
  systemctl restart atc-dashboard.service && echo "\$envh" > \$st/env && echo "restarted for $ENVF"
fi
SCRIPT
chmod 0755 "$UPDATER"
cat > /etc/systemd/system/atc-update.service <<UNIT
[Unit]
Description=Pull atc-at-home from GitHub and restart the dashboard when it changes
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=*
StateDirectory=atc-update
ExecStart=$UPDATER
UNIT
cat > /etc/systemd/system/atc-update.timer <<'UNIT'
[Unit]
Description=Check for atc-at-home updates every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
UNIT
chmod 0644 /etc/systemd/system/atc-{dashboard.service,update.service,update.timer}
systemctl daemon-reload
systemctl enable atc-dashboard.service >/dev/null
systemctl restart atc-dashboard.service
mkdir -p /var/lib/atc-update
git -c safe.directory='*' -C "$SITE" rev-parse HEAD:dashboard > /var/lib/atc-update/code
sha256sum "$ENVF" | cut -c1-64 > /var/lib/atc-update/env
systemctl enable --now atc-update.timer >/dev/null

for _ in $(seq 1 30); do
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null && { echo "atc-dashboard is up on $PORT"; exit 0; }
  sleep 1
done
echo "atc-dashboard did not answer; see: journalctl -u atc-dashboard -n 50" >&2
exit 1
