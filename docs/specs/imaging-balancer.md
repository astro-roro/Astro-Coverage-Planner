# Spec: a monthly balancer that keeps imaging promises

Status: draft, 2026-10-23. Not built. The owner answered the open questions the same day, and his answers are in "Decisions" at the end.

ACP gains a balancer. Once each afternoon it looks at the month so far. It then changes the Target Scheduler (TS) priority of the projects the user has tagged. It never changes their state. The night's "ACP: Sync for tonight" push then carries those changes to TS. The point is to keep two promises. The monthly vote winner gets its hours. Dr Fesen's remnants get a fair share without taking the whole month. The planetary nebula survey fills whatever is left.

The user's own words set the shape. The survey is deliberately over-planned filler. The vote winner matters most "because I said I would do something", and stays high "until the poll stuff is covered then drop the priority". Fesen's work matters but "doesn't need to take up the whole time". A rough 35/35/30 split for science, survey and pretty, and "it doesn't need to be an exact split".

## Terms

- **Project**: a TS project, which in ACP is the group of plans that share a `project_name` (`docs/specs/ts-project-settings.md` section 1). Priority and state live on every plan in the group. The balancer works on projects, never on single plans.
- **Role**: what a project is for, in the balancer's eyes. One of `science`, `survey` or `pretty`. A project with no role is invisible to the balancer.
- **Commitment**: a promise with a goal in hours per filter and a month. Two kinds: `vote`, for the monthly vote winner, and `pin`, for any other pretty project the user wants finished.
- **Month**: a calendar month of nights. A night belongs to the month of its evening date, using the same night rule as the Activity tab (cut-over at local noon in `ACP_ACTIVITY_TZ`). So the night of 31 October belongs to October, even though most of it falls on 1 November.
- **Elapsed**: the share of the month's nights that are already over. On the afternoon of night N in a month of D nights, elapsed is (N − 1) / D. Tonight does not count.
- **Share**: a role's hours this month divided by the hours of every project with a role this month. Every project whose plans agree on a role counts, whatever its state or hold. A project whose plans disagree on role counts nowhere.
- **Hold**: a per-project switch that tells the balancer never to change that project.

## What is true today

These facts shape every rule below.

- `PUT /api/projects/<project_name>/settings` (`app.py`, `api_project_settings`) writes state and priority to every plan in a project in one write to `plans.json`, and records a `project_settings` Activity event. The balancer reuses that write path.
- The writers resolve a project whose plans disagree: the highest priority wins, and the most running state wins (`_build_ts_export`). Both the extension (`nina_ts_sync/convert.py`) and the plugin (`TsHeldStates.cs`) follow `ts-project-settings.md`.
- On push, TS's `priority` and `state` columns are written only when ACP changed them since the last sync on that rig (`ts-project-settings.md` section 5). The writer tells by comparing ACP's value with the rig's base snapshot. A change the balancer makes in `plans.json` therefore counts as an ACP change and reaches TS on the next push.
- The push that runs every night is the plugin's sequence instruction "ACP: Sync for tonight" (`Sequencer/SyncForTonight.cs`). It sits before the TS container and calls `/api/plans/match` with `supports_state: true`. It loads active plans that fit, and sets the state of held plans on their existing rows.
- Each progress report from the plugin updates an Activity event with key `progress_report:<plan_id>:<night>`. Its `data.filters[<filter>].delta_h` holds the hours that filter gained that night (`_record_progress` in `app.py`). Summing `delta_h` over a month's nights gives each plan's hours for the month. Activity keeps every night unless `ACP_ACTIVITY_KEEP_NIGHTS` is set.
- A plan in the vote has `public_section: "vote"`, `vote_goals` (hours per filter) and a server-set `vote_baseline`. The live page counts vote progress as the hours now minus the baseline (`shooting_publish._vote_filters`, `current_done_hours`). No field marks a vote winner today.
- `target_class` is `survey`, `deep` or `pretty`. There is no science class. Fesen's G7.7-3.7 would be `deep`, but so could any other deep target.
- ACP already runs an in-process scheduler thread for archive scans (`ACP_SCAN_CRON`), so a second scheduled job needs no cron daemon.
- TS chooses targets with weighted scoring rules, and project priority is one of those rules. A priority change shifts the odds; it does not force an order. Inactive is the only hard lever, and the balancer does not use it. So the split stays a soft target, which is what the owner asked for. This is from TS's documentation, not checked against its source for this spec.

## 1. Commitments and roles

### Data model

Three new optional fields on a plan, validated in `_validate_plan_payload`. Like priority, `balance_role` and `balance_hold` are written to every plan in the project through `PUT /api/projects/<project_name>/settings`, which gains both keys.

| Field | Values | Absent means |
|---|---|---|
| `balance_role` | `"science"`, `"survey"`, `"pretty"` | the balancer ignores the project |
| `balance_hold` | boolean | false |
| `commitment` | object, below | no commitment |

A `commitment` lives on one plan, and applies to that plan's project:

| Key | Type | Notes |
|---|---|---|
| `kind` | `"vote"` or `"pin"` | |
| `month` | `"YYYY-MM"` | the month the promise is for |
| `goals` | object, filter name to hours | each hours value greater than 0 |
| `baseline` | object, filter name to hours | set by the server, never the client |
| `set_at` | ISO date | set by the server |
| `met_at` | ISO date or absent | set by the balancer the first time every goal is met |

A plan with a commitment must have `balance_role: "pretty"`. Saving one without it is a 400.

The baseline is the hours the plan already had per goal filter when the commitment was made. For a vote winner it is copied from `vote_baseline`, so the balancer and the live page agree on progress. For a pin, or a vote winner with no `vote_baseline`, it is `current_done_hours` at that moment. It is stored on the commitment so it survives the plan leaving the vote section, which clears `vote_baseline`.

Progress on a goal filter is `current_done_hours` for that filter minus its baseline, floored at 0 and capped at the goal. The commitment's done fraction is the sum of capped progress over the sum of goals. It is met when every goal filter is at its goal.

### Settings

Balancer settings and its own memory live in `balancer.json` beside `plans.json`, written atomically.

| Setting | Default | Meaning |
|---|---|---|
| `mode` | `"dry_run"` | `"off"`, `"dry_run"` or `"live"` |
| `run_at` | `"14:00"` | local time in `ACP_ACTIVITY_TZ` |
| `shares` | science 35, survey 35, pretty 30 | target percentages, summing to 100 |
| `tolerance` | 10 | percentage points either side of a share |
| `min_month_hours` | 5 | below this many hours in the month, no share is judged |
| `finish_by` | 0.75 | the elapsed point by which a commitment should be met |

The memory holds `last_run_night` and the band each role was in last run, for hysteresis (section 2).

### UI

All small, in `static/app.js`.

- The plan editor's Constraints fieldset gains a Balancer role select beside Priority and State, with None, Science, Survey and Pretty. It also gains a "Balancer leaves this alone" checkbox for hold. The existing line about shared projects names them too.
- When the plan is in the vote, the Public page section gains a "Won the vote for" button. Its month select defaults to the current month. It creates a `vote` commitment from `vote_goals`, and sets the role to pretty.
- Any pretty plan gets "Pin as a commitment" in the same section. It shows the same table of hours per filter as the vote, defaulting to the hours left to each filter's goal, and a month select.
- A plan with a commitment shows one line: "Vote winner for November: 1.5 of 6 h, behind pace" or "met 18 Nov". A "Remove commitment" button clears it. Only the user can remove one.
- Under Priority, a plan the balancer changed shows "Balancer set this to High on 10 Nov: the November vote winner is behind pace." Hovering gives the rule number.
- The Activity tab header gains a Balancer button. It opens a small dialog with the settings and the current shares and commitments. Its "Preview" button shows what a run would change now, and "Run now" runs it.

### Behind pace

A commitment is behind pace when its done fraction is less than the required fraction, where required is elapsed / `finish_by`, capped at 1. With the default 0.75, a 6 h goal on the afternoon of 10 November (elapsed 9/30) needs 2.4 h done.

Once elapsed reaches `finish_by`, required is 1. From then on an unmet commitment is always behind pace, so it goes to high and stays there until it is met. In a 30-night month that starts on night 24. This is the owner's rule for the last week: bump the promise to high and do what the weather allows.

A commitment whose month has passed and that is still not met is carried over. It counts as behind pace every day until it is met or the user removes it.

A commitment for a future month is ignored until that month starts.

The first build uses no forecast. Pace is judged on elapsed nights alone.

## 2. The rules

### Bands

For each role, the balancer computes its share and puts it in a band.

- Over: share above target plus tolerance.
- Under: share below target minus tolerance.
- In: anything else.

Two guards keep it calm.

- While the month's hours across all roles are under `min_month_hours`, every role is In.
- Hysteresis: a role that was Over stays Over until its share drops to its target or below. A role that was Under stays Under until its share reaches its target. This stops a project flipping priority every afternoon around the edge of a band.

The guard is applied first. When it forces every role to In, memory records In, so each month starts with clean bands.

A role with no projects is left out. The other roles' targets are scaled up in proportion so they still sum to 100. A role with no projects therefore never sits at Under and never moves anything.

### Urgent or calm

The month is urgent when any commitment is behind pace. Otherwise it is calm. TS has only three priority levels, so this is how the balancer gets the order the user asked for: vote winner, then Fesen, then survey. In a calm month Fesen holds high and the survey normal. When a commitment falls behind, everything else steps down one level to make room above them.

### Rules, in order

The balancer walks every project with a role. The first rule that matches decides the project's priority.

1. **Skip.** The project is on hold, or any of its plans is `closed` or `draft`, or its plans disagree on role. Its priority does not change. A disagreement adds a warning to the run report. A skipped project still has its commitment judged, so `met_at` and the needs-you events in section 3 still update.
2. **Commitment met.** The project carries a commitment that is met: priority low. The balancer sets `met_at` the first time. Other commitments on the same project, if any, are judged on their own, and an unmet one wins.
3. **Commitment behind pace:** priority high.
4. **Commitment on pace:** priority normal.
5. **Science.**

   | | Calm | Urgent |
   |---|---|---|
   | Over | normal | low |
   | In or Under | high | normal |

6. **Survey.**

   | | Calm | Urgent |
   |---|---|---|
   | Over | low | low |
   | In or Under | normal | low |

   Survey is never high.

7. **Pretty, no commitment.**

   | | Calm | Urgent |
   |---|---|---|
   | Under | normal | low |
   | In or Over | low | low |

Hours on a committed project count toward the pretty share. A project whose only commitment is skipped for missing data (section 4) is treated as pretty with no commitment, under rule 7.

### State

The balancer never changes state. It does not pause, activate, draft or close any project. A project the user set inactive stays inactive, though its priority is still kept up to date so it is right when the user turns it back on.

## 3. When it runs, what it writes, how it reports

### Schedule

A scheduler thread, started the same way as the scan scheduler, runs the balancer once a night at `run_at`. The default is 14:00 local time. At 15:00 the observatory's nightly launcher closes and restarts NINA, which runs "ACP: Sync for tonight". A run takes seconds, so 14:00 leaves an hour to spare. If ACP starts after `run_at` and before 23:00 and tonight's run has not happened, it runs on start. A run after 15:00 still writes `plans.json`, but its changes reach TS only at the next night's sync, and the report says so. `last_run_night` stops a second scheduled run on the same night. "Run now" always runs.

`mode: "off"` stops the thread doing anything. Turning the balancer off leaves every priority and state as it last set them.

### Inputs

- `plans.json`, read under the plans lock.
- Month hours per project: the sum of positive `delta_h` over `progress_report` events for that project's plans, on nights in the current month.
- Commitment progress: `current_done_hours` for the commitment's plan, as the live page counts it.

The decision is a pure function in a new `balancer.py`: plans, month hours, commitment progress, settings, memory and the night go in; the list of changes, the new memory and the report come out. It has no Flask and no file access, so the tests in section 5 run as plain tables.

### Writing

In `live` mode, all of a run's changes go to `plans.json` in one write under the plans lock, through the same helper `api_project_settings` uses, with its own `project_settings` events suppressed. Each changed plan gets `updated_at`, and `balancer_note` (`{rule, at, text}`) for the editor line. `balancer.json` is written after `plans.json` succeeds.

Nothing else moves. The next "ACP: Sync for tonight" sees ACP's priority differ from the rig's base and writes it, by the existing rules. If the user changed priority in TS at the rig and has not sent TS to ACP since, the balancer's value wins on push, as any ACP change does. Hold is the way to protect a project from that.

In `dry_run` mode, `plans.json` is untouched. The run still updates the band memory and `last_run_night`, so dry-run reports read the same as live ones would. A commitment met during a dry run is recorded in memory as `met_seen` with its date, instead of `met_at` on the plan, so the report says "met" once. The needs-you events open and resolve in dry run exactly as in live, because they report facts about the month, not changes the balancer made.

### Reporting

Every run records one Activity event, type `balancer_run`, key `balancer:<night>`, in the Plans chip. A "Run now" later the same night updates the same event.

- Title: "Balancer: changed 4 projects", "Balancer: no changes", or "Balancer (dry run): would change 4 projects".
- Detail: one line per change, with the reason in numbers. Lines are ordered by rule number, then by project name, so commitments come first. "NGC 300: priority normal to high. November vote winner has 1.5 of 6 h, and 2.4 h is the pace for 30 percent of the month gone." "G7.7-3.7: priority high to normal. The vote winner comes first while it is behind."
- Then one line per role: "Science 6.0 h, 39 percent (target 35). Survey 8.0 h, 52 percent, over. Pretty 1.5 h, 10 percent, under."
- Then any warnings, each with severity `warn` on the event.
- `data` holds the same as numbers: shares, bands, each commitment's done and required, and each change with `{project, field, from, to, rule}`.

Two needs-you events, both opened and resolved by the balancer:

- A commitment not met when its month ends. Key `balancer_missed:<plan_id>:<month>`, title "NGC 300 missed its November vote goal: 4.0 of 6 h". Resolved when the commitment is met or removed.
- Three runs in a row skipped for missing data (section 4). Key `balancer_blind:<night>`, resolved by the next run that completes. This cannot be raised while Activity is off; that case shows only in the log and the Balancer dialog.

## 4. Safety

- **Dry run first.** The balancer ships with `mode: "dry_run"`. The user switches to live after reading a few reports.
- **Hold.** A project with `balance_hold: true` never has its priority changed, in any mode. Its commitment is still judged, as rule 1 says. Its hours still count toward its role's share.
- **Opt-in by role.** A project with no role is never read for shares and never changed. A new plan has no role.
- **Nothing deleted.** The balancer writes only `priority`, `balancer_note` and a commitment's `met_at`. It never deletes a plan, a commitment or an event, and never closes a project.
- **No state changes.** The balancer never changes a project's state (section 2).
- **Missing data.**

  | Missing | What happens |
  |---|---|
  | `plans.json` unreadable or invalid | the run stops, nothing changes, warning logged |
  | Activity off (`ACP_ACTIVITY=off`) | no month hours and nowhere to report: the run stops, nothing changes, warning logged, and the Balancer dialog says why |
  | No progress reports yet this month | every role is In through the `min_month_hours` guard; commitments still use `current_done_hours` |
  | No recent progress report | the age is tonight's night minus the newest night with any `progress_report` event. At 4 or more, the run goes ahead with a warning naming the age. Stale hours make a commitment look behind, which only raises its priority |
  | A commitment's plan has no goal filters it can count | that commitment is skipped with a warning; the others run |
  | Run throws | nothing is written, the exception is logged, and the run counts as skipped |

## 5. Tests

ACP core, unittest, in `tests/test_balancer.py`. All but the last group call the pure function.

### Fixture

November 2026, 30 nights. Settings at their defaults, in `live` mode. Five projects, one plan each, all active:

| Project | Role | Priority at start | Commitment |
|---|---|---|---|
| G7.7-3.7 | science | high | none |
| PN-A | survey | normal | none |
| PN-B | survey | normal | none |
| NGC 300 | pretty | low | vote, 2026-11, Ha 3 h and OIII 3 h, baseline 0 |
| M42 | pretty | low | none |

Hours below are month hours by role, and the vote winner's done hours. "Required" is the vote winner's required hours for that night. Each scenario starts from this fixture with empty memory, unless it says it follows another.

### Scenarios

1. **Start of month.** Night 1 Nov, no hours. Required 0, done 0, so on pace. Calm, every role In by the guard. NGC 300 low to normal. Nothing else changes.
2. **Behind pace.** Night 10 Nov, elapsed 0.30, required 2.4 h, done 1.5 h. Science 6, survey 8, pretty 1.5, total 15.5. Urgent. Science 39 percent In, survey 52 percent Over, pretty 10 percent Under. NGC 300 normal to high. G7.7-3.7 high to normal. PN-A and PN-B normal to low. M42 stays low. The report's first line names NGC 300 with 1.5 of 6 h against 2.4 h.
3. **Cloudy week.** Follows scenario 2, with no progress reports for nights 10 to 16 Nov. Night 17 Nov, elapsed 0.53, required 4.3 h, done 1.5 h. No changes. Title "Balancer: no changes". The report warns that the last progress report is 8 nights old.
4. **Vote goal met.** Follows scenario 3. Night 20 Nov, done Ha 3.0 and OIII 3.2, so 6 of 6 capped. Science 10, survey 9, pretty 9, total 28. Science 36 percent In. Survey 32 percent leaves Over, since it is at or below its target. Pretty 32 percent leaves Under, since it has reached its target. Calm. NGC 300 high to low, and `met_at` is 2026-11-20. G7.7-3.7 normal to high. PN-A and PN-B low to normal. M42 stays low. Running again on 21 Nov leaves NGC 300 low.
5. **Fesen ahead.** Night 12 Nov, vote on pace (done 4.0, required 2.9). Science 14, survey 4, pretty 7, total 25. Calm. Science 56 percent Over: G7.7-3.7 high to normal. Survey 16 percent Under: stays normal, never high. Pretty 28 percent In: M42 stays low. NGC 300 low to normal, by rule 4.
6. **Fesen ahead, hysteresis.** After scenario 5, science at 40 percent on 13 Nov stays Over, and G7.7-3.7 stays normal. At 34 percent on 14 Nov it is In, and G7.7-3.7 goes back to high.
7. **Fesen behind, calm.** Night 15 Nov, vote on pace (done 4.0, required 3.7). Science 2 of 20 h, 10 percent Under. G7.7-3.7 stays high.
8. **Fesen behind, urgent.** The same hours with the vote winner behind pace. G7.7-3.7 goes to normal while NGC 300 is high. The vote winner comes before Fesen.
9. **End of month.** Night 30 Nov, elapsed 0.97, required 6 h, done 4.0 h, so behind. Science 30, survey 40, pretty 10, total 80. Urgent. Science 38 percent In, survey 50 percent Over, pretty 12.5 percent Under. NGC 300 low to high. G7.7-3.7 high to normal. PN-A and PN-B normal to low. M42 stays low. No state changes.
10. **Last week bump.** Science 20, survey 20, pretty 18, total 58, all In. Night 23 Nov, elapsed 0.73, required 5.87 h, done 5.9 h, so on pace and calm. NGC 300 low to normal. G7.7-3.7 stays high, and PN-A and PN-B stay normal. Then night 24 Nov with no new hours: elapsed 0.77, required 6 h, so behind and urgent. NGC 300 normal to high. G7.7-3.7 high to normal. PN-A and PN-B normal to low. No state changes on either night.
11. **Month rollover.** Follows scenario 9. Night 1 Dec, still 4.0 of 6. The commitment carries over and is behind, so the month is urgent. NGC 300 stays high, G7.7-3.7 stays normal, and PN-A and PN-B stay low. A needs-you event "NGC 300 missed its November vote goal: 4.0 of 6 h" is open. Every role is In by the guard. When the goal is met on 6 Dec, that event resolves and NGC 300 goes low.
12. **State never moves.** Across scenarios 1 to 11, every project ends in the state it started in. As scenario 10, but with PN-B set inactive by the user on 22 Nov: on 24 Nov PN-B goes normal to low beside PN-A and stays inactive.
13. **Hold.** M42 has hold and the user set it high. In every scenario above it stays high, and its hours still count toward pretty.
14. **Skips.** A project with a closed plan, a draft plan, or two plans with different roles is never changed. The role disagreement shows as a warning.
15. **Dry run.** Scenario 2 in `dry_run` mode. `plans.json` is byte-identical before and after. The event title is "Balancer (dry run): would change 4 projects", and its `data` lists the same four changes as the live run.
16. **No double run.** Running twice on one night with no new hours changes nothing the second time, and there is still one `balancer_run` event for the night.
17. **Future and pinned commitments.** Scenario 2 plus a `pin` on M42 for November, Ha 2 h, baseline 0, done 0.5 h. Required is 0.8 h, so the pin is behind too, and M42 goes low to high beside NGC 300. Separately, a `vote` commitment on M42 for December is ignored throughout November, and M42 follows rule 7.
18. **Missing data.** With Activity off, the run stops and `plans.json` is unchanged. Separately, scenario 2's hours with NGC 300's commitment naming only a filter the plan cannot count: the commitment is skipped with a warning, and no commitment is behind, so the month is calm. G7.7-3.7 stays high. PN-A and PN-B normal to low, since survey is Over. Pretty is Under, so NGC 300 and M42 both go low to normal under rule 7.
19. **Stale age.** Newest progress report on night 9 Nov. The run on 12 Nov (age 3) gives no warning. The run on 13 Nov (age 4) warns "last progress report 4 nights old".

### Routes and wiring

20. `PUT /api/projects/<name>/settings` accepts `balance_role` and `balance_hold`, rejects other role values with 400, and writes them to every plan in the project.
21. Saving a plan with a commitment and no pretty role is a 400. A client-sent `baseline`, `set_at` or `met_at` is overwritten by the server.
22. "Won the vote for" copies `vote_goals` and `vote_baseline` into the commitment. Moving the plan back to the current section afterwards leaves the commitment's baseline alone.
23. A live run writes one change to `plans.json` per project, records no `project_settings` events, and the group's priority in `_build_ts_export` output matches what the balancer set.
24. The scheduler runs at 14:00 and not at 13:59, and a second tick on the same night does nothing.

No change is needed in the extension or plugin. Their existing tests for "ACP changed it since the base" cover the push.

## Decisions

The owner answered the open questions on 2026-10-23.

1. Shares are 35/35/30 for science, survey and pretty, with a tolerance of 10 points.
2. A commitment aims to be met three quarters of the way through its month (`finish_by` 0.75).
3. An unmet commitment carries into the next month at high priority, with a needs-you item.
4. The balancer never pauses the survey or changes any state. An unmet promise in the last week simply goes to high. In his words: "if it's not done with 1 week to go we just bump the priority to high and do what we can, might also have just been bad weather or something."
5. Projects get their first role by hand in the editor, plus a one-off "tag from target class" button mapping survey to survey and pretty to pretty. Science is always set by hand.
6. The balancer ships in dry-run mode for the first month.
7. No forecast in the first build.
8. It runs at 14:00 local time, an hour before the nightly launcher restarts NINA and runs "ACP: Sync for tonight" at 15:00.
9. The balancer's priority may overwrite a change made by hand in TS at the rig. Hold protects a project from that.

## Out of scope

- Writing the balancer code.
- Changing TS's scoring weights.
- Changing project state.
- A clear-night forecast. The Nights extension on the server (`/api/ext/nights/`) simulates coming nights from plans and could feed pace later. Its output was not reachable for this spec.
- Per-night decisions. The balancer runs once each afternoon and does not react during a night.
- Balancing within a role, for example between two survey projects.
