# MCAS Agent Teams Governance

## Architecture: The Mesh

Three Claude Code sessions run simultaneously. Each session is a **Lead** that spawns two sub-agents. That's 9 Claudes total — 3 leads + 6 workers. Sub-agents only talk to their own lead (Claude Code limitation), but leads coordinate with each other via the shared filesystem.

```
Session 1 (Lead)          Session 2 (Lead)          Session 3 (Lead)
  ├── Agent A (Compute)     ├── Agent A (Compute)     ├── Agent A (Compute)
  └── Agent B (Analysis)    └── Agent B (Analysis)    └── Agent B (Analysis)
         \                        |                        /
          \                       |                       /
           -------- .claude/taskboard/ ------------------
```

**Key principle**: Any lead can post work to the taskboard. Any lead can claim it for its own agents. The sub-agents don't know or care which session originated the task — they just do what their lead assigns. This makes all 6 workers available to all 3 leads.

### Session Identity
Each session picks an ID on startup: `S1`, `S2`, `S3`. This ID prefixes all task files and claimed entries. Pick the lowest unclaimed number by checking which sessions have recent activity in the taskboard.

---

## Shared Taskboard Protocol

The taskboard lives at `.claude/taskboard/` with three subdirectories:

```
.claude/taskboard/
  pending/     ← any lead drops tasks here
  claimed/     ← moved here when a lead picks it up
  done/        ← moved here with result pointer when complete
```

### Task File Format

Filename: `{priority}_{timestamp}_{session}_{short_name}.md`
Example: `high_20260220_153012_S1_rsi_sweep.md`

Content:
```
REQUESTED_BY: S1
TASK: Run parameter sweep on RSI thresholds [20,25,30,35,40] x [60,65,70,75,80] for BTC/USDT 1h
SCOPE: Backend/research/sweeps/
OUTPUT_TO: Backend/research/results/rsi_sweep_20260220.json
PRIORITY: high
CONSTRAINT: Check cpu_query.py brief every 3 minutes. If steal >5%, reduce concurrency.
AGENT_TYPE: compute|analysis|either
DEPENDS_ON: [file path or task filename that must exist/complete first, or "none"]
```

### Task Lifecycle

1. **Post**: Lead writes task file to `pending/`
2. **Claim**: Lead moves file from `pending/` to `claimed/`, appends `CLAIMED_BY: S2` and `CLAIMED_AT: [timestamp]` to the file
3. **Execute**: Claiming lead assigns task to one of its own sub-agents
4. **Complete**: Lead moves file from `claimed/` to `done/`, appends `COMPLETED_AT: [timestamp]` and `RESULT: [one line]`
5. **Consume**: Requesting lead reads the OUTPUT_TO file for full results

### Claim Rules
- **First-mover wins.** If you `mv` the file and it succeeds, it's yours. If it fails (file gone), another session got it first — move on.
- **Respect AGENT_TYPE.** If a task says `compute`, assign it to your Compute agent. If `analysis`, your Analysis agent. `either` means use whoever is free.
- **Respect DEPENDS_ON.** Don't claim a task until its dependency exists in `done/` or the referenced output file exists on disk.
- **Prefer your own tasks.** If your session posted it and no one else has claimed it, you can claim it yourself. No obligation to farm everything out.
- **Don't hoard.** Only claim what your agents can start within the next few minutes. If both agents are busy, leave tasks in `pending/` for other sessions.

### Polling Protocol
Leads check `pending/` for new work:
- Every time a sub-agent completes a task and is free
- Every 3-5 minutes if all agents are busy (to reprioritize if a `high` task appeared)
- Immediately after posting a task that depends on another session's output (to see if anything else is available while waiting)

---

## Team Structure (Per Session)

### Lead (the Claude Code session itself)
- **Role**: Task decomposition, cross-session coordination, assignment to own agents, synthesis
- **Never does**: Run long scripts, backtests, heavy computation
- **Always does**: Read result files from disk, manage taskboard, assign next tasks, maintain the big picture
- **Context discipline**: Your context window is the bottleneck. Protect it ruthlessly.

### Agent A — Compute
- **Role**: Backtests, parameter sweeps, data pipelines, any script expected to take >30s
- **Owns**: Script execution, performance measurement, result file generation
- **Reports to lead**: Status + one-line result + output file path. Nothing else unless asked.
- **Resource protocol**: Check CPU before, during, and after every long-running script.

### Agent B — Analysis & Infrastructure
- **Role**: Reviews Compute output files, statistical analysis, database operations, infrastructure changes, pipeline preparation
- **Owns**: Interpretation of results, next-stage pipeline setup, DB queries
- **Reports to lead**: Conclusion + recommendation + file path. Nothing else unless asked.
- **Can read**: Any file any agent across any session produces. Read from disk, never ask anyone to paste content.

---

## Communication Protocol

### The Cardinal Rule
**All substance lives on disk. Chat channels carry pointers and summaries, not data.**

This applies within sessions (lead <-> agents) and across sessions (via taskboard). Non-negotiable.

### Within-Session: Agent -> Lead Check-in
```
STATUS: [complete|in_progress|blocked|failed]
RESULT: [one line, under 50 tokens]
OUTPUT: [file path to full results]
CPU_PROFILE: [from cpu_query.py brief, if script ran >60s]
READY: [yes|awaiting_review|need_input]
```

### Within-Session: Lead -> Agent Task Assignment
```
TASK: [clear description of what to do]
SCOPE: [which files/directories you own for this task]
OUTPUT_TO: [where to write results]
PRIORITY: [high|normal|low]
CONSTRAINT: [any resource or time constraints]
ORIGIN: [self|S1|S2|S3 — which session requested this work]
```

### Cross-Session: Via Taskboard Only
Leads do NOT message each other directly. All cross-session coordination goes through the taskboard files. This prevents context bloat and creates an audit trail.

To signal urgency: use `high` priority prefix in filename. Other leads will see it on next poll.

To signal a blocker: create a task file with `DEPENDS_ON: <other_task_filename>`. This tells all leads "don't start this until that's done."

To broadcast status: each lead maintains its own `.claude/team-state-S{N}.md`. Other leads can read it for context without exchanging messages.

### Peer Communication (Agent -> Agent, Same Session)
Agents within the same session CAN message each other directly. Same rules:
- Under 50 tokens for the message itself
- Reference files by path, never paste content
- Use only when the lead doesn't need to know yet

### Lead Context Budget Management
The lead tracks approximate context usage. When a teammate checks in:
1. Lead processes the update (costs ~100 tokens)
2. Lead decides: assign next task OR say "hold -- freeing context"
3. If lead needs detail from a result file, it reads the file directly -- never asks the agent to summarize in chat

If the lead estimates it's above 70% context usage:
- Tell agents to hold new check-ins
- Synthesize current state into its session state file
- Write summary to `.claude/team-state-S{N}.md`
- Continue with clean context

---

## Resource Awareness Protocol (CPU Monitoring)

The VPS runs a CPU metrics collector writing to Redis every 5 seconds. All agents query it via:

```bash
docker exec mcas-cpu-collector python cpu_query.py <command>
```

### Commands
| Command | Use When | Returns |
|---------|----------|---------|
| `brief` | Before/during any script >60s | One-line: CPU%, breakdown, load, top container |
| `now` | Need full snapshot | Aggregate + per-core + load average |
| `summary` | Reviewing a period | Rolling 5m/15m/1h stats (mean/max/min/p95) |
| `containers` | Diagnosing which service is heavy | Per-container CPU/mem, sorted by usage |
| `hotspots` | Diagnosing contention | Per-core imbalance + top consumers |
| `history <seconds>` | Post-mortem on a script run | Time series for last N seconds |
| `health` | Verifying collector is alive | Status + data freshness |
| `alerts` | Checking if sustained high CPU | Threshold breach state |

### Interpretation Guide
| Signal | Meaning | Action |
|--------|---------|--------|
| `usage <40%` on multi-core | Single-threaded bottleneck | Stop, refactor for parallelism |
| One core 100%, others idle | GIL-bound or single-process | Use multiprocessing, not threading |
| `steal >5%` | Hypervisor throttling (noisy neighbors) | Reduce concurrency, schedule off-peak |
| `steal >15%` | Severe throttling | Pause heavy work, alert Darren |
| `iowait >20%` | Disk/network bound | More parallelism won't help; optimize I/O |
| `load > 2x cores` | Oversubscribed | Queue work, don't pile on |

### Multi-Session Resource Coordination
With 9 Claudes and up to 6 concurrent scripts, CPU contention is real.
- **Before claiming a compute-heavy task**: check `brief`. If load > 12 (75% of 16 cores), leave the task for later or post a `low` priority version.
- **If steal > 5%**: ALL sessions reduce concurrency. Post a `high` priority task in `pending/` with `TASK: RESOURCE_ALERT — steal detected, all sessions throttle` so other leads see it.
- **Stagger heavy work.** If you see another session's Compute agent is running (check their team-state file), prefer assigning Analysis work to your agents until the heavy job finishes.
- **Total concurrent heavy scripts: max 2 across all sessions.** A heavy script = expected >5 min wall time. Leads coordinate by checking each other's team-state files.

### Mandatory Resource Checks
- **Compute agent**: Check `brief` before every script >60s. Check every 2-3 min during execution. Log `history <duration>` after completion.
- **Analysis agent**: Check `brief` before any DB-heavy query. Check `containers` if response feels slow.
- **Lead**: Can ask any agent to report their last CPU profile. Include in task assignment if resource-sensitive.

---

## File Ownership Rules

### Within-Session
1. **Lead assigns SCOPE with every task.** Agents only write within their assigned scope.
2. **Result files go to designated OUTPUT_TO paths.** Never write results to a location another agent might be writing to.
3. **Shared config files (docker-compose.yml, CLAUDE.md, etc.) are lead-only.** Agents propose changes; lead applies them.

### Cross-Session
4. **Each session owns its own team-state file**: `.claude/team-state-S{N}.md`. Read others' files freely, never write to them.
5. **Taskboard is shared**: any lead can write to `pending/`, but only the claiming lead moves files through `claimed/` -> `done/`.
6. **Output paths must be session-namespaced** when there's any risk of collision. Convention: include session ID in output filenames, e.g., `rsi_sweep_S1_20260220.json`.
7. **Lock convention**: Before modifying a shared file (not taskboard — those use atomic `mv`), create `<filename>.lock` with your session ID. Delete when done. If a lock file exists, wait and check back in 10 seconds.
8. **Shared config files are lead-only, single-session-at-a-time.** If two leads need to edit docker-compose.yml, one locks it, edits, unlocks. The other waits.

---

## Error Handling

### Agent Fails a Task
Report to lead:
```
STATUS: failed
RESULT: [what went wrong, one line]
OUTPUT: [error log path if applicable]
READY: need_input
```
Lead decides: retry, reassign to other agent, post back to taskboard for another session, or escalate to Darren.

### Cross-Session Task Failure
If a claimed task fails and your session can't resolve it:
1. Move the task file back to `pending/` with `FAILED_BY: S{N}` and `FAIL_REASON: [one line]` appended
2. Another session can claim it and try a different approach
3. If a task returns to `pending/` twice, it gets escalated — leave it in `pending/` with `ESCALATE: true` appended, and all leads flag it for Darren

### Agent Loses Context / Session Issues
If a session dies:
1. Its team-state file (`.claude/team-state-S{N}.md`) persists as the recovery record
2. Any tasks in `claimed/` by that session are stale — another lead can move them back to `pending/` after 10 minutes of no activity
3. A replacement session picks up by reading the dead session's team-state file and reclaiming its work

### CPU Alert During Script
If `cpu_query.py alerts` shows `alert_active: true` (sustained >85% for >30s):
1. Compute agent pauses or reduces concurrency
2. Reports to lead with CPU profile
3. Lead posts resource alert to taskboard if other sessions should know
4. Lead decides whether to continue, throttle, or abort

---

## Session Startup Checklist

When starting a Claude Code session as part of the mesh:

1. **Pick your session ID.** Check `.claude/taskboard/claimed/` and `.claude/team-state-S*.md` for active sessions. Take the lowest available of S1, S2, S3.
2. **Verify CPU collector**:
   ```bash
   docker exec mcas-cpu-collector python cpu_query.py health
   ```
3. **Check VPS load**:
   ```bash
   docker exec mcas-cpu-collector python cpu_query.py brief
   ```
4. **Read other sessions' state files** (`.claude/team-state-S*.md`) to understand what's already in flight.
5. **Scan the taskboard**: check `pending/` for unclaimed work, `claimed/` for in-progress work, `done/` for recently completed results.
6. **Create/update your team-state file**: `.claude/team-state-S{N}.md`
7. **Spawn your two agents** (Compute + Analysis).
8. **Start working**: claim tasks from `pending/`, post new tasks, or begin the work Darren assigned.

---

## Anti-Patterns (Do Not Do These)

- Agent pastes 200 lines of backtest output into chat with lead
- Lead asks agent "what were the results?" instead of reading the output file
- Two agents (any session) writing to the same directory without coordination
- Running a 30-minute script without any CPU check-ins
- Agent sends check-in over 100 tokens to the lead
- Lead runs a heavy script itself instead of assigning to Compute
- Agents communicate through the lead when a direct peer message is faster
- Anyone ignores steal >5% -- this means the VPS is being throttled
- Two sessions claim the same task (use atomic `mv`, not copy-then-delete)
- A lead hoards 5 pending tasks while other sessions' agents sit idle
- Sessions write to each other's team-state files
- Three compute-heavy scripts running simultaneously (max 2)

## Good Patterns (Do These)

- Agent writes full results to JSON, sends lead a 30-token summary
- Lead reads result files directly when it needs detail
- Compute agent checks CPU before, during (every 2-3 min), and after scripts
- Analysis agent starts preparing the next pipeline stage while Compute is still running
- Lead maintains `.claude/team-state-S{N}.md` as recovery documentation
- Agents use lock files for any shared resource
- Lead protects its context -- pushes back if agents try to dump data
- Leads check other sessions' team-state files before claiming heavy compute tasks
- Sessions stagger heavy work based on CPU load
- Failed cross-session tasks return to pending with failure context for the next claimant
- Leads poll the taskboard whenever an agent finishes work
