# Agent Teams Governance — Patch v2

**Hand this to Claude Code (any session). It patches the existing governance doc and settings.**

---

## Problem 1: Polling Loop Burns Context

The `sleep 10 && ls` poll pattern consumed 40+ tool calls of pure waste per session.
Replace with OS-level file watching that blocks silently, consuming zero tokens.

### Install inotifywait on VPS (one-time)
```bash
sudo apt-get install -y inotify-tools
```

### Replace the polling protocol in `.claude/agent-teams-governance.md`

Find the section about polling and replace with:

```markdown
## Waiting for Dependencies (File Watch Protocol)

**NEVER poll in a loop.** Polling with `sleep && ls` burns tokens doing nothing.

When blocked on a file that doesn't exist yet, use `inotifywait` to block at the OS level:

```bash
# Wait for a specific file to appear (blocks silently, zero token cost)
inotifywait -e create,moved_to --include "ignition_events_with_outcomes.csv" /srv/mcas/results/ 2>/dev/null
```

```bash
# Wait for ANY new file in the taskboard pending directory
inotifywait -e create,moved_to /home/darren/Desktop/MCAS/.claude/taskboard/pending/ 2>/dev/null
```

```bash
# Wait for a specific task to move to done/
inotifywait -e create,moved_to --include "baseline" /home/darren/Desktop/MCAS/.claude/taskboard/done/ 2>/dev/null
```

**Rules:**
- inotifywait blocks the bash call until the event fires. No looping. No token burn.
- Set a timeout if you don't want to wait forever: `inotifywait -t 300 ...` (5 min max)
- When it fires, THEN check what appeared and decide your next action.
- If timeout expires with no event, check team state files for updates, then re-enter the watch.

**While waiting for inotifywait, use your OTHER agent for prep work** (see "Blocked But Not Idle" below).
```

---

## Problem 2: Permission Prompts Break Autonomy

Sessions freeze on "Do you want to proceed?" because Claude Code needs explicit permission for bash commands.

### Update `.claude/settings.json`

Merge these allowlist rules into the existing settings file. This tells Claude Code which commands are pre-approved:

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  },
  "permissions": {
    "allow": [
      "Read",
      "Edit",
      "Write",
      "MultiEdit",
      "Bash(ls *)",
      "Bash(cat *)",
      "Bash(head *)",
      "Bash(tail *)",
      "Bash(wc *)",
      "Bash(grep *)",
      "Bash(find *)",
      "Bash(stat *)",
      "Bash(du *)",
      "Bash(sort *)",
      "Bash(echo *)",
      "Bash(mv *)",
      "Bash(mkdir *)",
      "Bash(cp *)",
      "Bash(cd *)",
      "Bash(docker *)",
      "Bash(python3 *)",
      "Bash(python *)",
      "Bash(inotifywait *)",
      "Bash(sleep *)",
      "Bash(touch *)"
    ]
  }
}
```

**KNOWN BUG**: Claude Code uses prefix matching on the entire command string. Piped commands like `ls /path | wc -l` won't match `Bash(ls *)` because the full string is `ls /path | wc -l`, not just `ls`. Each individual command in a pipe needs its own allow rule, but that still won't prevent the prompt on the piped combination.

**Recommended approach**: Given this bug and the sheer number of compound commands the agents run (every poll was `sleep 10 && ls ... 2>&1 && echo ...`), the cleanest fix is to start sessions with:

```bash
claude --dangerously-skip-permissions
```

This removes ALL approval prompts. It's safe for your setup because:
1. The governance doc constrains SCOPE — agents only write where assigned
2. The agents are operating on a research repo, not production code
3. The CLAUDE.md Hard Nevers (§2) already prevent destructive operations
4. You're on a VPS with backups, not a local machine

If you want belt-and-suspenders, use `--dangerously-skip-permissions` but add deny rules for the truly dangerous commands:

```json
{
  "permissions": {
    "deny": [
      "Bash(rm -rf *)",
      "Bash(sudo rm *)",
      "Bash(docker rm *)",
      "Bash(docker volume rm *)",
      "Bash(DROP *)"
    ]
  }
}
```

---

## Problem 3: Blocked But Not Idle

When all taskboard items are blocked, agents burned context polling instead of doing useful prep work.

### Add this section to `.claude/agent-teams-governance.md`

```markdown
## Blocked But Not Idle

If you have nothing to claim from the taskboard, DO NOT poll. Instead:

1. **Set an inotifywait** on the taskboard or results directory (zero-cost wait)
2. **Use your OTHER agent for prep work** while the watch blocks. Prep work is scoped, non-conflicting work that positions you to move faster when unblocked:

   Prep work examples (ALWAYS allowed):
   - Read and understand data schemas you'll need later
   - Write analysis plans / feature engineering plans to markdown
   - Build script scaffolding (functions, imports, structure) WITHOUT running it
   - Profile existing result files to understand formats
   - Read CLAUDE.md methodology sections you'll need for validation
   - Document findings from completed work

   Prep work anti-patterns (NEVER do these):
   - Don't write to anyone else's SCOPE
   - Don't run scripts that modify the database
   - Don't claim tasks that aren't formally unblocked
   - Don't start compute work that depends on files that don't exist yet

3. **When inotifywait fires**, check what appeared, claim if appropriate, dispatch agents.

This way one agent does the blocking wait (zero cost) while the other does prep (actual value). Two agents, never both idle.
```

---

## Problem 4: Task DAG Too Serial (Governance Guidance)

### Add this to the Lead responsibilities in `.claude/agent-teams-governance.md`

```markdown
## Task Decomposition Rules (Lead Only)

When posting tasks to the taskboard:

1. **Maximise independent work.** If 3 sessions are running, there should be at least 3 tasks claimable in parallel at all times. If your DAG has a single bottleneck that blocks everything else, you've designed it wrong.

2. **Split audits from extraction.** An audit (what's in the DB?) and an extraction (build the dataset) are separate tasks. Don't chain them if they can be parallelised.

3. **Post prep tasks alongside blocked tasks.** If Task B is blocked on Task A, post a "Prep Task B" that does the non-dependent parts (schema analysis, harness scaffolding, methodology review). This gives idle sessions useful work.

4. **Never post a task that only one session can claim.** If a task requires context that only S1 has, either include that context in the task file or make S1 do it. Every task should be claimable by any session.

5. **Dependency depth limit: 3.** If your DAG is deeper than 3 levels, flatten it. Sequential chains of 6+ tasks (like the current W1→W2→W3 chain) guarantee that 2 of 3 sessions are idle at any given time.
```

---

## Summary of Changes

| File | Change |
|------|--------|
| `.claude/settings.json` | Add permissions allowlist for autonomous bash execution |
| `.claude/agent-teams-governance.md` | Replace poll loop with inotifywait protocol |
| `.claude/agent-teams-governance.md` | Add "Blocked But Not Idle" prep work rules |
| `.claude/agent-teams-governance.md` | Add task decomposition rules for leads |
| VPS (one-time) | `apt-get install inotify-tools` |

## Verification

After patching, test:
```bash
# Test inotifywait works
inotifywait -t 5 -e create /tmp/ &
touch /tmp/testfile_$(date +%s)
# Should see: /tmp/ CREATE testfile_...

# Test permissions (start a claude session and confirm no approval prompts for):
ls /srv/mcas/results/
docker exec mcas-cpu-collector python cpu_query.py brief
mv /home/darren/Desktop/MCAS/.claude/taskboard/pending/test.md /home/darren/Desktop/MCAS/.claude/taskboard/claimed/test.md
```
