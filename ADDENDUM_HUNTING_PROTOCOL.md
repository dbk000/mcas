# Agent Teams Governance — Addendum: Autonomous Research Protocol

**Add this as a new top-level section in `.claude/agent-teams-governance.md`**

---

```markdown
## The Prime Directive: Never Idle

You have access to 365 days of OHLCV across 588 pairs, on-chain flow data, sentiment 
data from 5 sources, and a 16-vCPU VPS with live performance telemetry. Idle time is 
unacceptable. The opportunity cost of an Opus 4.6 instance doing nothing exceeds the 
cost of it trying something that fails.

**Check CPU utilisation via `docker exec mcas-cpu-collector python cpu_query.py brief` 
regularly. If aggregate usage is below 50% and you have available agents, you are 
under-utilising the VPS. Create work.**

### The Three States

Every agent is always in exactly one state:

1. **EXECUTING** — Running an assigned task from the taskboard
2. **HUNTING** — No taskboard work available; autonomously exploring data for edge
3. **WATCHING** — One agent per session on inotifywait; the OTHER agent must be HUNTING

There is no fourth state. There is no "idle." There is no "polling." There is no "waiting."

### Hunting Protocol

When an agent enters HUNTING state, it picks from this priority list:

**Priority 1: Unexplored data corners**
- Query tables nobody has audited yet
- Find columns with signal potential that no task has addressed
- Cross-reference data sources (e.g., do sentiment spikes in social_posts 
  correlate with anything in whale_transactions?)
- Look for temporal patterns nobody asked about (day-of-week effects, 
  hour-of-day effects, pre/post-funding-rate-snapshot behaviour)

**Priority 2: Stress-test existing findings**
- Take any result file in results/ and try to break it
- Run the same analysis on a different time window
- Check if a finding survives different market regimes (trending vs ranging)
- Test sensitivity to parameter choices (what happens if you shift a window by ±20%?)

**Priority 3: Novel feature discovery**
- Engineer features nobody asked for
- Combine data sources in ways the task spec didn't suggest
- Look for non-linear relationships (interactions, ratios, regime switches)
- Test unconventional predictors (e.g., does the *variance* of sentiment 
  predict better than the *level*? Does funding rate *acceleration* matter 
  more than the rate itself?)

**Priority 4: Infrastructure improvements**
- Profile slow queries and suggest indexes
- Identify data gaps that could be backfilled
- Write utility functions that would speed up future research
- Document undocumented schemas or data quirks

### Hunting Rules

1. **All hunting output goes to `results/hunting/`** — create the directory if needed.
   Filename format: `{session}_{agent}_{timestamp}_{topic}.json`
2. **Hunting never modifies production code or existing results.** Read-only on 
   everything except your output directory.
3. **If a hunt finds something with IC > 0.02, immediately post a task to the 
   taskboard** for proper walk-forward validation. Don't validate it yourself 
   during a hunt — that's a task.
4. **Hunting is interruptible.** The moment a taskboard item becomes claimable, 
   drop the hunt mid-stride, claim the task, and execute. Hunting is the lowest 
   priority activity but it is NOT optional.
5. **Log every hunt.** Even failures are data. Write a one-paragraph summary to 
   `results/hunting/hunt_log.md` (append, don't overwrite):
   ```
   ## [S2-A] 2026-02-20 19:30 — Funding rate acceleration as ignition filter
   Tested whether d(funding_rate)/dt in the 24h before ignition predicts outcomes.
   Coverage: 847/30794 events (2.7%) — too sparse from derivatives data.
   IC: not computable (insufficient overlap). DEAD END due to data depth.
   Time spent: ~3 minutes. VPS CPU during: 34%.
   ```

### VPS Utilisation Targets

| Metric | Target | Action if below |
|--------|--------|-----------------|
| Aggregate CPU | >50% during active research | More agents should be HUNTING |
| Per-core max | At least 2 cores >80% | Scripts are single-threaded; refactor |
| steal% | <5% | Acceptable; proceed |
| steal% | >10% | Reduce concurrency, alert Darren |
| iowait% | >15% | DB-bound; check for missing indexes |
| Load average (1m) | >8 (on 16 cores) | Good utilisation |
| Load average (1m) | <4 (on 16 cores) | Under-utilised; create more parallel work |

### The Lead's Responsibility

The lead monitors aggregate VPS utilisation. If CPU is under 50%:

1. Check how many agents are HUNTING vs WATCHING vs EXECUTING
2. If any agent is in a state other than these three, fix it
3. If all agents are HUNTING and CPU is still low, the hunts are too 
   lightweight — push them toward compute-heavier exploration 
   (parameter sweeps, cross-validation, feature interaction matrices)
4. Post the current utilisation to your team-state file every 15 minutes:
   ```
   ## VPS Utilisation [19:30]
   CPU: 67% | load: 10.2/8.4/6.1 | steal: 0.1%
   Agents: 2 EXECUTING, 3 HUNTING, 1 WATCHING
   Top containers: mcas-worker@234%, postgres@89%, python3@156%
   ```

### What "Creating Work" Looks Like in Practice

If S1 finishes its sentiment feature screen and S2 finishes on-chain, and both are 
waiting for the walk-forward harness — that's 4 idle agents across 2 sessions. Instead:

- S1-A: Hunt for day-of-week effects in ignition success rates
- S1-B: inotifywait on harness completion
- S2-A: Hunt for whether social_posts velocity in the 6h window outperforms 24h
- S2-B: Profile the walk-forward query pattern for optimization opportunities

CPU should be at 60%+ even while "waiting." The inotifywait costs nothing. The hunts 
are burning compute productively. The moment the harness lands, one agent per session 
drops the hunt and claims the next task. The other continues hunting.

This is not optional behaviour. This is how the hive works.
```
