# Working on Astro Coverage Planner

## Closing issues

Only write `Fixes #N` or `Closes #N` in a commit or PR when the fix has been verified end to end by us. GitHub closes the issue the moment that PR merges, which is wrong when the reporter still has to confirm it on their machine. For a user-reported bug, write `Refs #N` instead, ask the reporter to recheck, and close the issue by hand once they reply. Issue #46 was closed early this way on 2026-09-03.

## Platforms

Most users run ACP on Windows next to NINA. The maintainer's machines and Docker are macOS and Linux. Anything that touches the filesystem, console encoding, or file types can behave differently on Windows, so the test suite runs on both in CI and a Windows-only report deserves a Windows reproduction before being blamed on the user's setup.

## Keeping private details out

ACP is a public repository, so nothing private goes into code, tests, fixtures, docs, commit messages or PR text. That covers real host and machine names, home network addresses and paths, NAS share names, personal identities and emails, private science targets and collaborators, private pipelines and their names, and snapshots of real databases. Use placeholders instead: RIG-A and LAPTOP for machines, "the home server" for servers, `/data/archive` for paths, SNR-A for targets, "a processing pipeline" for pipelines. Test fixtures are generated from made-up data and never copied from a live database. Commits use the GitHub noreply identity. A private term list outside the repository backs this up: `scripts/check_private_terms.py` checks added lines and commit messages in CI and in the hooks under `.githooks/`, and it never prints the term it found.
