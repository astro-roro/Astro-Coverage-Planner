# Spec: a monthly balancer that keeps imaging promises

Status: built 2026-10-23, shipping in dry-run mode. The owner answered the open questions the same day, and an independent review followed. Both are folded in, and the answers are in "Decisions" at the end.

ACP gains a balancer. Once each afternoon it looks at the month so far. It then changes the Target Scheduler (TS) priority of the projects the user has tagged. It never changes their state. The night's "ACP: Sync for tonight" push then carries those changes to TS. The point is to keep two promises. The monthly vote winner gets its hours. Dr Fesen's remnants get a fair share without taking the whole month. The planetary nebula survey fills whatever is left.

The user's own words set the shape. The survey is deliberately over-planned filler. The vote winner matters most "because I said I would do something", and stays high "until the poll stuff is covered then drop the priority". Fesen's work matters but "doesn't need to take up the whole time". A rough 35/35/30 split for science, survey and pretty, and "it doesn't need to be an exact split".

## Terms

- **Project**: a TS project, which in ACP is the group of plans that share a `project_name` (`docs/specs/ts-project-settings.md` section 1). Priority and state live on every plan in the group. The balancer works on projects, never on single plans.
- **Role**: what a project is for, in the balancer's eyes. One of `science`, `survey` or `pretty`. A project with no role is invisible to the balancer.
- **Commitment**: a promise with a goal in hours per filter and a month. The first build has one kind, `vote`, for the monthly vote winner.
- **Current commitment**: a met commitment is current only in its own month. An unmet one from this month or an earlier month stays current until it is met. A commitment for a later month is ignored until its month starts. After its month, a met winner is an ordinary project again: it follows rules 4 to 6, and its "vote goal met" line stops appearing in reports.
- **Month**: a calendar month of nights. A night belongs to the month of its evening date, using the same night rule as the Activity tab (cut-over at local noon in `ACP_ACTIVITY_TZ`). So the night of 31 October belongs to October, even though most of it falls on 1 November.
- **Elapsed**: the share of the month's nights that are already over. On the afternoon of night N in a month of D nights, elapsed is (N − 1) / D. Tonight does not count.
- **Share**: a role's hours this month divided by the hours of every project with a role this month. Every project whose plans agree on a role counts, whatever its state or hold. A project whose plans disagree on role counts nowhere.
- **Hold**: a per-project switch that tells the balancer never to change that project's priority.

## What is true today

These facts shape every rule below.

- `PUT /api/projects/<project_name>/settings` (`app.py`, `api_project_settings`) writes state and priority to every plan in a project in one write to `plans.json`, and records a `project_settings` Activity event. Its loop that applies the values and collects the changes is inline in the route today.
- The writers resolve a project whose plans disagree: the highest priority wins, and the most running state wins (`_build_ts_export`). Both the extension (`nina_ts_sync/convert.py`) and the plugin (`TsHeldStates.cs`) follow `ts-project-settings.md`.
- On push, TS's `priority` and `state` columns are written only when ACP changed them since the last sync on that rig (`ts-project-settings.md` section 5). The writer tells by comparing ACP's value with the rig's base snapshot. A change the balancer makes in `plans.json` therefore counts as an ACP change and reaches TS on the next push.
- The push that runs every night is the plugin's sequence instruction "ACP: Sync for tonight" (`Sequencer/SyncForTonight.cs`). It sits before the TS container and calls `/api/plans/match` with `supports_state: true`. It loads active plans that fit, and sets the state of held plans on their existing rows.
- Each progress report from the plugin updates an Activity event with key `progress_report:<plan_id>:<night>`. Its `data.filters[<filter>].delta_h` holds the hours that filter gained that night (`_record_progress` in `app.py`). Summing `delta_h` over a month's nights gives each plan's hours for the month. Activity keeps every night unless `ACP_ACTIVITY_KEEP_NIGHTS` is set.
- A plan in the vote has `public_section: "vote"`, `vote_goals` (hours per filter) and a server-set `vote_baseline`. The live page counts vote progress as the hours now minus the baseline (`shooting_publish._vote_filters`, `current_done_hours`). No field marks a vote winner today.
- `PUT /api/plans/<id>` stores the plan as the client sent it. Only a page from before the goal panel gets stored fields copied back in (`_OLD_PAGE_KEEPS`). A field the editor does not know about is therefore lost on save unless the route keeps it.
- `target_class` is `survey`, `deep` or `pretty`. There is no science class. Fesen's G7.7-3.7 would be `deep`, but so could any other deep target.
- ACP already runs an in-process scheduler thread for archive scans (`ACP_SCAN_CRON`), so a second scheduled job needs no cron daemon.
- TS chooses targets with weighted scoring rules, and project priority is one of those rules. A priority change shifts the odds; it does not force an order. Inactive is the only hard lever, and the balancer does not use it. So the split stays a soft target, which is what the owner asked for. This is from TS's documentation, not checked against its source for this spec.

## 1. Commitments and roles

### Data model

Four new optional fields on a plan.

| Field | Values | Absent means | Written by |
|---|---|---|---|
| `balance_role` | `"science"`, `"survey"`, `"pretty"` | the balancer ignores the project | the user, through the project settings route |
| `balance_hold` | boolean | false | the user, through the project settings route |
| `commitments` | list of objects, below | no commitments | the commitment routes and the balancer |
| `balancer_note` | `{rule, at, text}` | no note | the balancer |

Like priority, `balance_role` and `balance_hold` are written to every plan in the project through `PUT /api/projects/<project_name>/settings`, which gains both keys and validates them.

Each entry in `commitments` applies to its plan's project:

| Key | Type | Notes |
|---|---|---|
| `kind` | `"vote"` | the only kind in the first build |
| `month` | `"YYYY-MM"` | the month the promise is for; unique within the list |
| `goals` | object, filter name to hours | each hours value greater than 0 |
| `baseline` | object, filter name to hours | set by the server |
| `set_at` | ISO date | set by the server |
| `met_at` | ISO date or absent | set by the balancer the first time every goal is met |

It is a list so that a carried-over goal and a new vote win can sit on one plan. A plan with any commitment must have `balance_role: "pretty"`.

The baseline is the hours the plan already had per goal filter when the commitment was made. It is copied from `vote_baseline`, so the balancer and the live page agree on progress. A plan with no `vote_baseline` takes `current_done_hours` at that moment. It is stored on the commitment so it survives the plan leaving the vote section, which clears `vote_baseline`.

Progress on a goal filter is `current_done_hours` for that filter minus the commitment's baseline, floored at 0 and capped at the goal. The done fraction is the sum of capped progress over the sum of goals. A commitment is met when every goal filter is at its goal. Two commitments on one plan each count from their own baseline, so hours added after both baselines count toward both.

### Routes

- `POST /api/plans/<id>/commitments`, body `{month, clear_other_votes?}`, creates a vote commitment from the plan's `vote_goals` and baseline, and sets the project's role to pretty. It is a 400 when the plan has no `vote_goals`, or already has a commitment for that month.
- `DELETE /api/plans/<id>/commitments/<month>` removes one. Only the user removes a commitment.
- `PUT /api/plans/<id>` never takes `commitments` or `balancer_note` from the client. It always keeps the stored values. It also keeps the stored `balance_role` and `balance_hold` when the payload leaves them out, the same way `_OLD_PAGE_KEEPS` works.

### The vote winner on the live page

Marking a winner also shows it on the live page, so readers can watch the previous month's vote gather data. The contract is in `docs/specs/shooting-page.md` ("Vote winner"). In short:

- A public plan with a current, unmet vote commitment publishes with `status: "vote_winner"`, `vote_month` and `vote_label`, whatever its last imaged date. Its `filters`, `done_hours` and `target_hours` use the commitment's goals and baseline, as the vote section does.
- Once the commitment is met, the plan falls back to the normal status rules with its full-plan numbers.
- A plan with a current commitment is never in the vote section, even if `public_section` is still `"vote"`. The commitment wins.
- The other vote options leave the vote section at the end of a round through the "Won the vote for" action. When other plans are still in the vote section, the editor asks "Also move the other N plans out of the vote section on the live page?". Yes sends `clear_other_votes: true` with the `POST`, and the server sets those plans to `public_section: "current"` and clears their `vote_baseline`. Their `vote_goals` stay, so they can be put back in next month. No answers the question with nothing changed.

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

The memory is listed in section 3, under "What it writes".

### UI

All small, in `static/app.js`.

- The plan editor's Constraints fieldset gains a Balancer role select beside Priority and State, with None, Science, Survey and Pretty. It also gains a "Balancer leaves this alone" checkbox for hold. The existing line about shared projects names them too.
- When the plan is in the vote, the Public page section gains a "Won the vote for" button. Its month select defaults to the current month.
- Each commitment shows one line: "Vote winner for November: 1.5 of 6 h, behind pace" or "met 18 Nov". Each line has a Remove button.
- Under Priority, a plan the balancer changed shows "Balancer set this to High on 10 Nov: the November vote goal is not covered yet." Hovering gives the rule number.
- The Activity tab header gains a Balancer button. It opens a small dialog with the settings and the current shares and commitments. Its "Preview" button shows what a run would change now, and "Run now" runs it. Run now decides its confirm prompt and its status line from the saved mode (the `mode` in the run result), never from the unsaved Mode select in the dialog.

### Pace

Pace does not set the vote winner's priority. An unmet current vote commitment is high from the first night of its month (rule 2). Pace decides what the reports say and when the user is told a promise is short.

A commitment is behind pace when its done fraction is less than the required fraction. Required is elapsed / `finish_by`, capped at 1. With the default 0.75, a 6 h goal on the afternoon of 10 November (elapsed 9/30) needs 2.4 h done. Once elapsed reaches `finish_by`, required is 1, so an unmet commitment is behind from then on. In a 30-night month that starts on night 24.

Pace drives three things:

- The editor line and the run report say "on pace" or "behind pace", with the numbers.
- On the first run with elapsed at or past `finish_by` and the commitment unmet, a needs-you item says it is short with a week to go (section 3). This is the last-week behaviour the owner chose: the promise is already high, so the balancer tells the owner and does what the weather allows.
- A commitment whose month has passed and that is still not met is carried over. It stays current, so it stays high until it is met or removed, and a needs-you item says it was missed.

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

### A role with a hole

A role has a hole when none of its projects is up: no project in it has gained hours in the last 7 nights (`RECENT_NIGHTS`, from progress reports) while being active and not closed or draft. Fesen's science target sitting below its minimum altitude in darkness is the usual case. Such a role cannot use its share, so the share goes to the others.

- The role's effective target becomes its actual share so far this month. The difference from its normal target is split evenly between the roles that are not in a hole. The effective targets still sum to 100.
- Hours the role already used this month keep counting in every share. Only its target moves.
- It applies to any role, not just science. With two roles in a hole, the third takes both spares. With no role in a hole nothing changes.
- A role at or above its normal target has no spare, so it is never changed by this rule.
- When every role is in a hole (nothing up anywhere, which a cloudy week also looks like), the run does not rebalance shares: rules 4 to 6 change nothing, and the report says "Nothing up in any role, so shares were not rebalanced." Commitment rules 2 and 3 still apply in full, so an unmet vote goal stays high, the last-week bump still happens, and a met goal still drops to low. No role is given a spare then. None of this applies while the `min_month_hours` guard is on, or when `recent_hours` is not supplied.
- Hold is deliberately ignored when judging "up": a held project that gained hours still counts as up, because the hours are real. Hold only stops the balancer changing its priority.
- Hysteresis uses the effective target, so a role leaving a hole may change band the day after.
- The run report says it: "Science has nothing up, so its spare 21 points are split: survey 45.4, pretty 40.4." The shares in `data` gain `base_target` and `hole`, and `data.holes` lists the roles.

Worked example, 23 October 2026, before the rule: science 4.5 h (14 percent), survey 9.9 h (31 percent), pretty 17.2 h (54 percent), total 31.6 h. Science is Under, survey is In, and pretty is Over (target 30, tolerance 10). Science has no hours in the last 7 nights, so the rule gives it a target of 14.2. The spare is 35 − 14.2 = 20.8 points, 10.4 each. Survey's target becomes 45.4, so it is Under at 31 percent. Pretty's becomes 40.4, so it is still Over at 54 percent. Science is In. Pretty projects are therefore still dropped to low by rule 6, because pretty has done 14 points more than even the enlarged share. The rule stops the survey being squeezed by an idle science role, but it does not by itself stop the pretty drop.

### Urgent or calm

The month is urgent while any project with a role has an unmet current vote commitment. Otherwise it is calm. Pace plays no part.

TS has only three priority levels, so this is how the balancer gets the order the user asked for: vote winner, then Fesen, then survey. While a vote goal is open, the winner is high, Fesen normal and the survey low. Once it is covered, Fesen goes back to high and the survey to normal.

### Rules, in order

The balancer walks every project with a role. The first rule that matches decides the project's priority.

1. **Skip.** The project is on hold, or any of its plans is `closed` or `draft`, or its plans disagree on role. Its priority does not change. A disagreement adds a warning to the run report. A skipped project still has its commitments judged, so `met_at` and the needs-you items in section 3 still update, and an unmet one still makes the month urgent.
2. **Vote goal open.** The project has an unmet current vote commitment: priority high.
3. **Vote goal covered.** The project has a current vote commitment, and every current one is met: priority low.
4. **Science.**

   | | Calm | Urgent |
   |---|---|---|
   | Over | normal | low |
   | In or Under | high | normal |

5. **Survey.**

   | | Calm | Urgent |
   |---|---|---|
   | Over | low | low |
   | In or Under | normal | low |

   Survey is never high.

6. **Pretty, no current commitment.**

   | | Calm | Urgent |
   |---|---|---|
   | Under | normal | low |
   | In or Over | low | low |

Hours on a committed project count toward the pretty share. A commitment skipped for missing data (section 4) counts as absent: it does not make the month urgent, and its project falls to rule 6 if it has no other current commitment.

### State

The balancer never changes state. It does not pause, activate, draft or close any project. A project the user set inactive stays inactive, though its priority is still kept up to date so it is right when the user turns it back on.

## 3. When it runs, what it writes, how it reports

### Schedule

A scheduler thread, started the same way as the scan scheduler, runs the balancer once a night at `run_at`. The default is 14:00 local time. At 15:00 the observatory's nightly launcher closes and restarts NINA, which runs "ACP: Sync for tonight". A run takes seconds, so 14:00 leaves an hour to spare.

If ACP starts after `run_at` and before 23:00, and tonight's run has not happened, it runs on start. A run after 15:00 still writes `plans.json`, but its changes reach TS only at the next night's sync, and the report says so. `last_run_night` stops a second scheduled run on the same night. "Run now" always runs.

`mode: "off"` stops the thread doing anything. Turning the balancer off leaves every priority as it last set them.

### Inputs

- `plans.json`, read under the plans lock.
- Month hours per project: the sum of positive `delta_h` over `progress_report` events for that project's plans, on nights in the current month.
- Commitment progress: `current_done_hours` for the commitment's plan, as the live page counts it.

- Recent hours per project: the same sum over the last 7 nights, to detect a role with a hole (section 2).

Month hours come from progress reports only. Hours that reach ACP only through an archive scan are not counted in shares. That is acceptable for three reasons. The balancer steers TS, and progress reports are what TS itself imaged. A scan can bring in older data or data from another rig, and has no reliable night to put it in. And the shares are soft targets, so a small undercount does no harm. Commitment progress does use archive hours, through `current_done_hours`, so a promise reads the same as on the live page.

The decision is a pure function in a new `balancer.py`: plans, month hours, commitment progress, settings, memory and the night go in; the list of changes, the new memory and the report come out. It has no Flask and no file access, so the tests in section 5 run as plain tables.

### The write helper

The loop in `api_project_settings` that applies values to a project's plans and collects the changes moves into a helper:

```python
def apply_project_settings(plans: list[dict], project_name: str, wanted: dict, now: str) -> dict:
    """Set every key in `wanted` on every plan whose project_name matches,
    stamp updated_at, and return {key: {"from": ..., "to": ...}} for the
    keys that changed. Mutates `plans` in place. Does not load, save or
    record anything."""
```

The route keeps its own load, save and `project_settings` event, and calls the helper in between. Its behaviour does not change. The balancer calls the helper once per changed project, saves `plans.json` once, and records no `project_settings` events. Its own `balancer_run` event is the record. Leaving the event out of the helper means no suppression flag is needed.

### What it writes

In `live` mode, all of a run's writes to `plans.json` happen in one save under the plans lock. `balancer.json` is written after that save succeeds.

| Where | Field | When |
|---|---|---|
| `plans.json`, every plan in a changed project | `priority` | the project's priority changes |
| same | `updated_at` | with any write to that plan |
| same | `balancer_note` | the project's priority changes |
| `plans.json`, the commitment's plan | `commitments[i].met_at`, plus `updated_at` | the first run that finds the commitment met |
| `balancer.json` | `last_run_night` | every run |
| same | `bands`, the band each role ended in | every run |
| same | `met_seen`, a map of plan id and month to date | dry run only, when a commitment is first found met |
| same | `skipped_runs`, a count | each skipped run adds one, and a run that completes sets it to 0 |
| Activity | one `balancer_run` event, and the needs-you items below | every run that is not stopped by Activity being off |

In `dry_run` mode nothing in `plans.json` is written. Everything in `balancer.json` and Activity is written as in live, so dry-run reports read the same as live ones would. A commitment met during a dry run goes into `met_seen` instead of `met_at`, so the report says "met" once. The needs-you items open and resolve in dry run too, because they report facts about the month, not changes the balancer made.

The next "ACP: Sync for tonight" sees ACP's priority differ from the rig's base and writes it, by the existing rules. If the user changed priority in TS at the rig and has not sent TS to ACP since, the balancer's value wins on push, as any ACP change does. Hold is the way to protect a project from that.

### Reporting

Every run records one Activity event, type `balancer_run`, key `balancer:<night>`, in the Plans chip. A "Run now" later the same night updates the same event.

- Title: "Balancer: changed 4 projects", "Balancer: no changes", or "Balancer (dry run): would change 4 projects".
- Detail: one line per change, with the reason in numbers. Lines are ordered by rule number, then by project name, so the vote winner comes first. "NGC 300: priority low to high. November vote winner has 1.5 of 6 h, behind the pace of 2.4 h." "G7.7-3.7: priority high to normal. The vote winner comes first until its goal is covered."
- Then one line per commitment with its pace, including any with no change.
- Then one line per role: "Science 6.0 h, 39 percent (target 35). Survey 8.0 h, 52 percent, over. Pretty 1.5 h, 10 percent, under."
- Then any warnings, each with severity `warn` on the event. When `ACP_ACTIVITY_KEEP_NIGHTS` is set below 31, every run warns: "Activity keeps only 20 nights, so this month's hours may be undercounted." The Balancer dialog shows the same line.
- `data` holds the same as numbers: shares, bands, each commitment's done and required, and each change with `{project, field, from, to, rule}`.

Three needs-you items, all opened and resolved by the balancer:

- Short with a week to go. Key `balancer_short:<plan_id>:<month>`, title "NGC 300 is short of its November vote goal with a week to go: 5.9 of 6 h". Opened on the first run with elapsed at or past `finish_by` and the commitment unmet. Resolved when it is met or removed.
- Missed. Key `balancer_missed:<plan_id>:<month>`, title "NGC 300 missed its November vote goal: 4.0 of 6 h". Opened on the first run after the month ends with the commitment unmet. Resolved when it is met or removed.
- Blind. Key `balancer_blind:<night>`, opened when `skipped_runs` reaches 3. Resolved by the next run that completes. This cannot be raised while Activity is off; that case shows only in the log and the Balancer dialog.

## 4. Safety

- **Dry run first.** The balancer ships with `mode: "dry_run"`. The user switches to live after reading a few reports.
- **Hold.** A project with `balance_hold: true` never has its priority changed, in any mode. Its commitments are still judged, as rule 1 says. Its hours still count toward its role's share.
- **Opt-in by role.** A project with no role is never read for shares and never changed. A new plan has no role.
- **Nothing deleted.** The balancer writes only the fields in "What it writes". It never deletes a plan, a commitment or an event, and never closes a project.
- **No state changes.** The balancer never changes a project's state (section 2).
- **Editor saves keep its fields.** A plan saved from the editor keeps `commitments` and `balancer_note` always, and `balance_role` and `balance_hold` when the payload leaves them out (section 1, Routes).
- **Missing data.**

  | Missing | What happens |
  |---|---|
  | `plans.json` unreadable or invalid | the run stops, nothing changes, warning logged |
  | Activity off (`ACP_ACTIVITY=off`) | no month hours and nowhere to report: the run stops, nothing changes, warning logged, and the Balancer dialog says why |
  | No progress reports yet this month | every role is In through the `min_month_hours` guard; commitments still use `current_done_hours` |
  | No recent progress report | the age is tonight's night minus the newest night with any `progress_report` event. At 4 or more, the run goes ahead with a warning naming the age. Stale hours make a vote goal look short, which never lowers its priority |
  | Activity keeps fewer than 31 nights | the run goes ahead with the warning in section 3 |
  | A commitment's plan has no goal filters it can count | that commitment is skipped with a warning and counts as absent; the others run |
  | Run throws | nothing is written, the exception is logged, and the run counts as skipped |

## 5. Tests

ACP core, unittest, in `tests/test_balancer.py`. Scenarios 1 to 21 call the pure function.

### Fixture

November 2026, 30 nights. Settings at their defaults, in `live` mode. Five projects, one plan each, all active:

| Project | Role | Priority at start | Commitments |
|---|---|---|---|
| G7.7-3.7 | science | high | none |
| PN-A | survey | normal | none |
| PN-B | survey | normal | none |
| NGC 300 | pretty | low | vote, 2026-11, Ha 3 h and OIII 3 h, baseline 0 |
| M42 | pretty | low | none |

Hours below are month hours by role, and the vote winner's done hours. "Required" is the vote winner's required hours for that night. Each scenario starts from this fixture with empty memory, unless it says it follows another.

### Scenarios

1. **Start of month.** Night 1 Nov, no hours. Required 0, done 0, so on pace. The vote goal is open, so the month is urgent. Every role is In by the guard. NGC 300 low to high. G7.7-3.7 high to normal. PN-A and PN-B normal to low. M42 stays low. The report says the vote winner is on pace.
2. **Behind pace.** Night 10 Nov, elapsed 0.30, required 2.4 h, done 1.5 h. Science 6, survey 8, pretty 1.5, total 15.5. Urgent. Science 39 percent In, survey 52 percent Over, pretty 10 percent Under. NGC 300 low to high. G7.7-3.7 high to normal. PN-A and PN-B normal to low. M42 stays low. The report's first line names NGC 300 with 1.5 of 6 h, behind the pace of 2.4 h.
3. **Cloudy week.** Follows scenario 2, with no progress reports for nights 10 to 16 Nov. Night 17 Nov, elapsed 0.53, required 4.3 h, done 1.5 h. No changes. Title "Balancer: no changes". The report warns that the last progress report is 8 nights old.
4. **Vote goal met.** Follows scenario 3. Night 20 Nov, done Ha 3.0 and OIII 3.2, so 6 of 6 capped. Science 10, survey 9, pretty 9, total 28. Science 36 percent In. Survey 32 percent leaves Over, since it is at or below its target. Pretty 32 percent leaves Under, since it has reached its target. The month is calm. NGC 300 high to low by rule 3, and `met_at` is 2026-11-20. G7.7-3.7 normal to high. PN-A and PN-B low to normal. M42 stays low. Running again on 21 Nov leaves NGC 300 low.
5. **Fesen ahead.** NGC 300's commitment was met on 8 Nov. Night 12 Nov. Science 14, survey 4, pretty 7, total 25. Calm. Science 56 percent Over: G7.7-3.7 high to normal. Survey 16 percent Under: stays normal, never high. Pretty 28 percent In: M42 stays low. NGC 300 stays low by rule 3. With the same hours and NGC 300's goal still open, the month is urgent instead: G7.7-3.7 high to low, PN-A and PN-B normal to low, NGC 300 low to high.
6. **Fesen ahead, hysteresis.** Follows scenario 5, with the goal met. Science at 40 percent on 13 Nov stays Over, and G7.7-3.7 stays normal. At 34 percent on 14 Nov it is In, and G7.7-3.7 goes back to high.
7. **Fesen behind, calm.** NGC 300's commitment was met on 8 Nov. Night 15 Nov. Science 2 of 20 h, 10 percent Under. G7.7-3.7 stays high.
8. **Fesen behind, vote on pace.** Night 15 Nov, done 4.0 h, required 3.7 h, so on pace but not met. Science 2 of 20 h. Urgent, since pace plays no part. G7.7-3.7 high to normal, and NGC 300 low to high. The vote winner comes before Fesen even when it is on pace.
9. **End of month.** Night 30 Nov, elapsed 0.97, required 6 h, done 4.0 h, so behind. Science 30, survey 40, pretty 10, total 80. Urgent. Science 38 percent In, survey 50 percent Over, pretty 12.5 percent Under. NGC 300 low to high. G7.7-3.7 high to normal. PN-A and PN-B normal to low. M42 stays low. No state changes. The short item for NGC 300 is open, since this is the first run past `finish_by`.
10. **Last week.** Science 20, survey 20, pretty 18, total 58, all In. Night 23 Nov, elapsed 0.73, required 5.87 h, done 5.9 h, so on pace. Urgent. NGC 300 low to high. G7.7-3.7 high to normal. PN-A and PN-B normal to low. No short item. Then night 24 Nov with no new hours: elapsed 0.77, required 6 h, so behind. No priority changes. The item "NGC 300 is short of its November vote goal with a week to go: 5.9 of 6 h" opens. It resolves on the run that finds the goal met.
11. **Month rollover.** Follows scenario 9. Night 1 Dec, still 4.0 of 6. The commitment carries over, stays current and unmet, so the month is urgent. NGC 300 stays high, G7.7-3.7 stays normal, and PN-A and PN-B stay low. The item "NGC 300 missed its November vote goal: 4.0 of 6 h" is open. Every role is In by the guard. When the goal is met on 6 Dec, the missed and short items resolve, and NGC 300 goes low.
12. **State never moves.** Across scenarios 1 to 11, every project ends in the state it started in. As scenario 10, but with PN-B set inactive by the user on 22 Nov: on 23 Nov PN-B goes normal to low beside PN-A and stays inactive.
13. **Hold.** M42 has hold and the user set it high. In every scenario above it stays high, and its hours still count toward pretty.
14. **Skips.** A project with a closed plan, a draft plan, or two plans with different roles is never changed. The role disagreement shows as a warning. NGC 300 on hold with its goal open still makes the month urgent, and stays at whatever priority it had.
15. **Dry run.** Scenario 2 in `dry_run` mode. `plans.json` is byte-identical before and after. The event title is "Balancer (dry run): would change 4 projects", and its `data` lists the same four changes as the live run. Scenario 4 in dry run records `met_seen` for NGC 300 and November, and writes no `met_at`.
16. **No double run.** Running twice on one night with no new hours changes nothing the second time, and there is still one `balancer_run` event for the night.
17. **Future commitment.** Scenario 2's hours, plus a vote commitment on M42 for December. It is ignored throughout November, so M42 falls to rule 6 and stays low.
18. **Missing data.** With Activity off, the run stops and `plans.json` is unchanged. Separately, scenario 2's hours with NGC 300's commitment naming only a filter the plan cannot count. The commitment is skipped with a warning and counts as absent, so the month is calm. G7.7-3.7 stays high. PN-A and PN-B normal to low, since survey is Over. Pretty is Under, so NGC 300 and M42 both go low to normal under rule 6.
19. **Stale age.** Newest progress report on night 9 Nov. The run on 12 Nov (age 3) gives no warning. The run on 13 Nov (age 4) warns "last progress report 4 nights old".
20. **Two commitments on one plan.** Follows scenario 11. On 1 Dec the user records a December vote win on NGC 300 with SII 4 h, baseline taken then. Both commitments are current and unmet: NGC 300 stays high. On 6 Dec November is met: its `met_at` is set and its missed item resolves, but December has SII 1 of 4, so NGC 300 stays high and the month stays urgent. On 15 Dec December is met: NGC 300 high to low and the month is calm. With every role In, G7.7-3.7 goes back to high and PN-A and PN-B to normal.
21. **Activity retention.** With `ACP_ACTIVITY_KEEP_NIGHTS=20`, every run warns that Activity keeps only 20 nights. With 31, or unset, there is no warning.

### Routes and wiring

22. `PUT /api/projects/<name>/settings` accepts `balance_role` and `balance_hold`, rejects other role values with 400, and writes them to every plan in the project. The existing tests for that route pass unchanged after `apply_project_settings` is extracted.
23. `POST /api/plans/<id>/commitments` with `{month: "2026-11"}` copies `vote_goals` and `vote_baseline` into a new commitment and sets the role to pretty. A second POST for the same month is a 400, and so is one for a plan with no `vote_goals`. `DELETE /api/plans/<id>/commitments/2026-11` removes only that entry.
24. An ordinary editor save, `PUT /api/plans/<id>` with a payload that leaves out `commitments`, `balancer_note`, `balance_role` and `balance_hold`, keeps all four as stored. A payload that sends a different `commitments` list or `balancer_note` is stored with the old values.
25. Moving a plan with a commitment back to the current section clears `vote_baseline` and leaves the commitment's baseline alone.
26. A live run saves `plans.json` once, records no `project_settings` events, and the group's priority in `_build_ts_export` output matches what the balancer set.
27. The scheduler runs at 14:00 and not at 13:59, and a second tick on the same night does nothing.

### Holes

Calls to the pure function with `recent_hours`, in `tests/test_balancer.py` (class `Holes`). Fixture: G7.7-3.7 science, PN-A survey, Dolphin Nebula pretty, night 23 October 2026, month hours 4.5, 9.9 and 17.2.

28. **Science hole.** Only PN-A and Dolphin Nebula gained hours in the last 7 nights. Science's target becomes its share (14.2), survey's 45.4 and pretty's 40.4, summing to 100. The report carries the "nothing up" line.
29. **The hole keeps its hours.** Same inputs. Science still shows 4.5 h and 14 percent, and its band is In.
30. **No hole.** All three roles gained hours recently. Targets stay 35, 35 and 30, there is no extra report line, and the changes equal a run without `recent_hours`.
31. **Two holes.** Only Dolphin Nebula gained hours recently. Pretty's target is 100 minus science's and survey's shares, and pretty is In.
32. **Nothing up anywhere, no data, over share.** No role gained hours recently: no share-driven changes, an unmet vote winner still goes high, and the report says "Nothing up in any role". `recent_hours` left out: no hole. Science at or above its target with nothing recent: no hole.

No change is needed in the extension or plugin. Their existing tests for "ACP changed it since the base" cover the push.

## Built notes

Choices made while building where the spec was silent. None changes what the telescope does.

- Run now when the mode is `off` is refused (409). Preview always works.
- Only scheduled runs set `last_run_night` and `last_attempt_night`, and only scheduled runs count toward `skipped_runs`. A manual "Run now" never blocks that day's scheduled run, and a failed manual run does not count against the balancer. A completed manual run still resets `skipped_runs` and resolves the blind item. `last_attempt_night` is one memory key beyond the spec: a failed scheduled run is not retried every poll. The scheduler also remembers its attempt in process, so a failure to save `balancer.json` cannot cause a retry loop.
- A project is keyed the way `_build_ts_export` keys it: its project name, else its target's name, else its plan id. Two unnamed plans with the same target name are one project. "Won the vote for" sets the pretty role through that key on every plan in the project, named or not.
- A run after 15:00 adds the line "This run was after 15:00, so its changes reach TS at the next night's sync." to its report when it changed something.
- A commitment whose `month` is not a valid `YYYY-MM` string is skipped with a warning. A malformed `commitments` or `goals` value is read as empty.
- `save_state` and Activity recording failures are logged and shown as warnings on the run result. They do not fail the run.
- Whether a commitment is met is judged on unrounded hours on the live page too, as the balancer does. Only the published numbers are rounded.
- The plan list and the commitment routes carry a derived `commitment_progress` (month, done_h, goal_h, met, behind), counted as the balancer counts. The editor's commitment line reads it. It is never stored.
- A project's role is the one role its plans agree on. A plan with no role among plans that share one is ignored for the vote, so a new plan in a tagged project does not make the project skip.
- A commitment that names some filters the plan cannot count is judged on the countable ones, with a warning naming the rest. With none countable it is skipped, as the spec says.
- A commitment stays met once `met_at` (or, in dry run, `met_seen`) is set, even if credited hours later dip.
- The editor's pace line uses the default `finish_by` of 0.75. The run report uses the saved setting and is the authority.
- The "tag from target class" button is `POST /api/balancer/tag-from-class`. It skips projects that already have a role and projects whose plans do not all share one class.
- Needs-you items and the run report use the Activity types `balancer_alert` and `balancer_run`, both under the Plans chip.
- `PUT /api/balancer/settings`, `GET /api/balancer`, `POST /api/balancer/preview` and `POST /api/balancer/run` back the dialog. `ACP_BALANCER=off` stops the scheduler thread from starting.

## Decisions

The owner answered the open questions on 2026-10-23, and an independent review settled the rest the same day.

1. Shares are 35/35/30 for science, survey and pretty, with a tolerance of 10 points.
2. A commitment aims to be met three quarters of the way through its month (`finish_by` 0.75).
3. An unmet commitment carries into the next month at high priority, with a needs-you item.
4. The question was: "Should the balancer pause the survey in the last week when a promise is still short?" The answer: "nah I think if it's not done with 1 week to go we just bump the priority to high and do what we can, might also have just been bad weather or something." So the balancer never pauses the survey or changes any state.
5. Projects get their first role by hand in the editor, plus a one-off "tag from target class" button mapping survey to survey and pretty to pretty. Science is always set by hand.
6. The balancer ships in dry-run mode for the first month.
7. No forecast in the first build.
8. It runs at 14:00 local time, an hour before the nightly launcher restarts NINA and runs "ACP: Sync for tonight" at 15:00.
9. The balancer's priority may overwrite a change made by hand in TS at the rig. Hold protects a project from that.
10. The vote winner is high "until the poll stuff is covered", whatever its pace. While its goal is open, Fesen sits at normal.
11. The first build has vote commitments only.

## Out of scope

- Writing the balancer code.
- Changing TS's scoring weights.
- Changing project state.
- Pinned commitments for pretty projects that did not win a vote. The `kind` field leaves room to add them later.
- A clear-night forecast. The Nights extension on the server (`/api/ext/nights/`) simulates coming nights from plans and could feed pace later. Its output was not reachable for this spec.
- Per-night decisions. The balancer runs once each afternoon and does not react during a night.
- Balancing within a role, for example between two survey projects.
