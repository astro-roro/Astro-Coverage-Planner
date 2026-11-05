# Contributing

## Running the tests

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests
```

## Private details check

This repository is public. Keep real machine names, network addresses, personal paths, email addresses and anything else about your own setup out of code, tests, docs and commit messages. Use generic placeholders such as RIG-A, LAPTOP or `/data/archive`, and build test fixtures from made-up data.

Maintainers keep a list of terms that must not appear. CI checks every pull request against it. To run the same check on your own machine, turn on the repository's hooks once per clone:

```
git config core.hooksPath .githooks
```

There are three hooks:

- `pre-commit` checks the lines your staged changes add.
- `commit-msg` checks the commit message.
- `pre-push` checks the commits the remote does not have yet: their added lines, their messages, and their author and committer names and emails. A new branch is compared with `origin/main`.

The hooks read the list from `~/.config/acp-private-terms.txt` and do nothing when that file is absent. The list holds one case-insensitive regular expression per line; blank lines and lines starting with `#` are ignored. You can also run the check by hand:

```
python scripts/check_private_terms.py --terms ~/.config/acp-private-terms.txt --diff
python scripts/check_private_terms.py --terms ~/.config/acp-private-terms.txt --all
```

A hit prints the file, the line and the rule number, never the matched text.
