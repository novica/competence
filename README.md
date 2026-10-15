# Competence

[![CI](https://github.com/novica/competence/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/novica/competence/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/competence.svg)](https://pypi.org/project/competence/)
[![Python](https://img.shields.io/pypi/pyversions/competence.svg)](https://pypi.org/project/competence/)
[![release-please](https://github.com/novica/competence/actions/workflows/release-please.yml/badge.svg?branch=main)](https://github.com/novica/competence/actions/workflows/release-please.yml)

A local web app for running a team skills matrix, following Andy Sawyer's method in
["Implementing a Skills Matrix Across Data Teams"](https://medium.com/@andy-sawyer/implementing-a-skills-matrix-across-data-teams-58675755e20d):

- **Skill level** on the Fibonacci scale 0/1/2/3/5/8, and **desire** on 1/2/3 (not interested, happy to do it,
  passionate). The article numbers desire 0/1/2; starting at 1 keeps a skill nobody is keen on from counting as 0.
- **Competency** = level × desire (0–24).
- Role targets, gaps, team flags, development goals, and comparisons between rounds.
- Certifications with expiry dates (held or not, required per role).

It runs on your own machine only (127.0.0.1), for one person: you, as facilitator. It works for any team: it ships
with catalogues for a data team (the default) and a software development team (used by the demo), and other teams,
technical or not, can start from their own catalogue file or an empty one.

The app's **Guide** (top menu) explains the method, how to run a round, and what every number and flag means.

## Install and start

Needs [uv](https://docs.astral.sh/uv/) (or pipx: `pipx install competence`, then leave out `uvx` below).

```
uvx competence init --demo    # once: demo database, a dev team of 6 invented people over 3 rounds
uvx competence --demo         # starts the app and opens http://localhost:8000  (Ctrl+C to stop)
```

For real use:

```
uvx competence init           # once: the real database, from the data-team catalogue (no people yet)
uvx competence                # starts the app
```

For a team that isn't a data team, start from the dev-team catalogue (front end / back end), a blank one or your
own file (the blank catalogue, `empty.toml` in the package, explains the layout):

```
uvx competence init --catalogue dev            # front end / back end developers
uvx competence init --catalogue empty          # one role, no skills: build the rest in Admin
uvx competence init --catalogue my-team.toml   # roles, categories, skills, targets, level texts
```

- **Where the data lives:** a per-user folder, `%LOCALAPPDATA%\competence` on Windows,
  `~/Library/Application Support/competence` on macOS and `~/.local/share/competence` on Linux. `--data DIR`
  (before `init`, e.g. `competence --data D:\matrix init`) uses another folder; pass it every time you start the app.
- **Catalogue files are read once.** After `init`, the database is the source of truth: change things in Admin.
  Admin → "Export the catalogue" saves what is in use (no people or ratings) as a file to start another database from.
- **Never overwrites:** `init` stops if the database file already exists.
- **Backs up on every start:** the app copies the database to `backups/` in the data folder before doing anything
  else. "Set all" in the targets grid backs up first too. There is no undo in the app: to go back, stop the app and
  copy a backup over the database file. These copies are on the same disk, so they don't replace an off-machine
  backup. The app keeps the newest 20 of its own backups per database and never touches other files in `backups/`,
  so a renamed copy (e.g. `matrix-before-cleanup.sqlite`) stays.
- **The demo database is disposable.** To reset it, delete `demo.sqlite` in the data folder and run
  `competence init --demo`.

### From a source checkout

Clone the repository and run the commands below from its folder; uv installs the dependencies on the first run.
`uv run app.py [--demo] [--reload]` and `uv run seed.py [--demo] [--catalogue NAME|FILE]` do the same as
`competence` and `competence init`, but keep the databases in the checkout's `data/` folder (gitignored).
`--reload` restarts the app when the code changes. Run the tests with `uv run pytest`.

## Data safety

- **Personal data:** the ratings are personal data about employees (GDPR). Keep the data folder on an encrypted disk
  (BitLocker on Windows). In a source checkout, `data/` is in `.gitignore`, so it's never committed.
- **Local only:** the app listens on 127.0.0.1 only. It refuses host names other than localhost, which blocks DNS
  rebinding, and it refuses form posts from other websites.
- **Nothing is lost:** there's a backup on every start, and nothing that has ratings is ever hard-deleted.

## Licence

[AGPL-3.0-or-later](LICENSE).
