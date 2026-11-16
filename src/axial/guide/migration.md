# Moving from bqdash to Axial

Axial is the new name of the engine. The command and Python package are `axial`,
configuration is `axial.yaml`, environment overrides start with `AXIAL_`, and new
projects store history in `.axial/`. Config format version 2 records this change.
Query, dashboard and history database formats are unchanged.

## Existing projects

Stop the running engine before moving its configuration or history. Commit or back
up the project first. Run these steps in each project's root directory:

1. Rename `bqdash.yaml` to `axial.yaml` and change its `version:` to `2`.
   Keep its warehouse, query and display settings. If the first Axial release is outside
   its `engine:` range, update that range to match the installed `axial --version`.
2. Rename `.bqdash/` to `.axial/` if present, including any SQLite WAL and SHM files.
   Change `storage.path` from `.bqdash/history.sqlite3` to `.axial/history.sqlite3`.
   If the project uses a custom history path, keep it; its database needs no migration.
3. Replace `.bqdash/` with `.axial/` in `.gitignore`. Keep the old ignore entry if
   an old directory remains, so history is never accidentally committed.
4. Rename `BQDASH_...` environment overrides to `AXIAL_...` in shell settings,
   scripts and secret stores. Change command invocations from `bqdash` to `axial`.
5. Run `axial agents sync`. It recognizes old `bqdash:managed` markers and replaces
   those generated files without `--force`. Handwritten files and `CONTEXT.md` are kept;
   update any old command names in those files yourself.
6. Run `axial check`, `axial agents sync --check` and `axial up --fixture`.
   Once the project works with Axial, uninstall the old tool with `uv tool uninstall bqdash`.

Axial reports a migration hint when it finds the old configuration, and does not move
files or create an empty replacement history database automatically.

## Installation

The public repository is now `https://github.com/lucasscariot/axial`. Existing GitHub
links redirect; update a project's engine checkout remote when convenient:

```sh
git remote set-url origin git@github.com:lucasscariot/axial.git
```

That command is for the engine repository, not a dashboard project's own repository.
Install through Homebrew with:

```sh
brew tap lucasscariot/axial https://github.com/lucasscariot/axial
brew install lucasscariot/axial/axial
```

The first Homebrew package pins the immutable Axial rename commit. Later releases
update the formula to their tagged archive through the release workflow. Tags through
`v0.1.2` contain the old bqdash package; they do not provide the `axial` command.
