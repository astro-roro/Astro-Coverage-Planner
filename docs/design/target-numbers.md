# Target numbers across rescans

The scanner clusters masters and sub folders into targets from scratch on every run. Each target still keeps its number from the last scan, so anything the planner stores against a number (finished marks, hidden marks) stays on the right target. The code is `scripts/target_ids.py`, called from `scripts/build_archive_manifest.py` once the target list is built.

## How a target keeps its number

The scanner reads the manifest it is about to replace and the ledger at `data/target_ids.json` (override the path with `ACP_TARGET_IDS`). Each new target is compared with every previous one. Two targets match when their centres sit within half the smaller field, or when their footprints overlap over at least half of the smaller one. If both carry object names and share none, they also need close centres and mostly the same footprint. That stops a small galaxy inside an old wide field being read as the wide field. Shared names win a tie.

- One old target matching one new target: the new one keeps the number.
- One old target matching several new ones (a split): the part with the most integration keeps the number. The rest get new numbers.
- Several old targets matching one new one (a merge): the new target keeps the number of the old one with the most integration. The others are recorded as merged into it.
- An old target with no match is retired. If a later scan finds a target on the same patch of sky (a drive was unplugged, say), the retired number comes back. It is never given to a different target.
- Anything else gets a new number, one above the highest ever issued.

The first scan after this change adopts the numbers in the existing manifest as they stand. With no manifest and no ledger, targets are numbered from 1.

## The ledger

`data/target_ids.json` holds the next number to issue, the retired and merged numbers, a map of merged number to surviving number, and each number's last known centre, footprint, names and hours. It exists so matching still works if the manifest is deleted or replaced by hand. It is written atomically, before the manifest.

## Per-target files and merges

When two targets merge, entries stored against the retired number move onto the surviving one. `TARGET_STORES` in `scripts/target_ids.py` lists the files this applies to:

- `data/target_overrides.json`, where a merged target is finished if either part was.
- `data/hidden.json`'s `targets` section, where a merged target is hidden if either part was. The `plans` and `projects` sections of that file are not touched by a merge; they are not keyed by target number.

A new per-target file registers itself by adding a `TargetStore` to that list with its path, the key of the dict inside the file that is keyed by target number, and a function that combines two entries. Nothing else needs to change.

## What the scan reports

The manifest carries `target_id_changes` with counts of numbers kept, new, retired, merged and brought back, plus the merge map. The same line prints at the end of the scan and heads `archive_manifest_summary.md`.
