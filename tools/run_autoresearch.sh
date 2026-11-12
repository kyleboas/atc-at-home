#!/bin/zsh
# Start the autoresearch agent detached, so it survives the chat session ending.
#   tools/run_autoresearch.sh [experiments]      default 30
# Progress: the dashboard (/), autoresearch.log (driver lines), agent.out (raw agent output).
cd "$(dirname "$0")/.." || exit 1
N=${1:-30}
PROMPT="You are the research agent in an autoresearch loop in $(pwd). Read program.md and follow it exactly: for each experiment call tools/experiment.py start, edit controller.py, then tools/experiment.py finish, then add a line to notes.md. Run $N experiments, then stop. Before the first experiment read README.md, atc/commands.py (the Infinite Flight command menus) and atc/sim.py, run tools/fetch_manual.py to get the ATC manual, and run tools/diagnose.py."
nohup claude -p "$PROMPT" --permission-mode bypassPermissions --model opus > agent.out 2>&1 &
PID=$!
disown
echo "{\"pid\": $PID, \"started\": $(date +%s), \"experiments\": $N}" > run.json
echo "$(date '+%H:%M:%S') agent started (pid $PID), $N experiments" >> autoresearch.log
nohup caffeinate -i -w $PID >/dev/null 2>&1 &
disown
echo "agent pid $PID"
