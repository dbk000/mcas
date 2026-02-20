#!/bin/bash
# CPU metrics hook — pushes VPS state into Claude Code context before every Bash call.
# This ensures Claude Code cannot forget to monitor resource utilisation.
# Data is pushed, not pulled — Claude sees it whether it wants to or not.

# Consume stdin (hook protocol requires reading it)
cat > /dev/null

# Fetch brief with 3s timeout — if collector is down, silently allow
BRIEF=$(timeout 3 docker exec mcas-cpu-collector python cpu_query.py brief 2>/dev/null) || exit 0
[ -z "$BRIEF" ] && exit 0

# Safely encode as JSON additionalContext
python3 -c "
import json, sys
brief = sys.argv[1]
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'allow',
        'additionalContext': 'VPS: ' + brief
    }
}))
" "$BRIEF"

exit 0
