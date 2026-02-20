#!/bin/bash
# CPU metrics hook — pushes VPS state into Claude Code context before every Bash call.
# This ensures Claude Code cannot forget to monitor resource utilisation.
# Data is pushed, not pulled — Claude sees it whether it wants to or not.

# Consume stdin (hook protocol requires reading it)
cat > /dev/null

# Fetch brief with 5s timeout — if collector is down, silently allow
# Note: docker exec takes longer in hook pipe context (closed stdin inheritance),
# so 5s is needed where 3s is insufficient.
BRIEF=$(timeout 5 docker exec mcas-cpu-collector python cpu_query.py brief < /dev/null 2>/dev/null) || exit 0
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
