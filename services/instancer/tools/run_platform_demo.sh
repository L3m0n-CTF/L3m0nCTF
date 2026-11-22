#!/usr/bin/env bash
# On the platform VM: run one command of platform_demo_round.py inside the application container (docs/runbooks/19-instancer-demo-round.md).
#   bash services/instancer/tools/run_platform_demo.sh <command> [/path/to/platform_demo_round.py]
# The script text is passed to the container's Python as an argument (the container's folders are read-only), and the plugin-loading noise is filtered out.
set -euo pipefail
CMD="${1:?usage: run_platform_demo.sh <setup_programme|setup|probe|counts|functional|burst|hostdown_during|hostdown_after|gone|after_gone|reset|expected_tcp|cleanup>}"
SCRIPT="${2:-$HOME/L3m0nCTF/services/instancer/tools/platform_demo_round.py}"
cd "$HOME/L3m0nCTF"
docker exec "$(tools/compose.sh ps -q ctfd)" l3mon-run /opt/venv/bin/python -c "$(cat "$SCRIPT")" "$CMD" 2>&1 | grep -vE "Loaded module|^ \* |DeprecationWarning|alembic|LegacyAPIWarning"
