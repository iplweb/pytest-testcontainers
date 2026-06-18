# Identifiable container names

**Status:** approved design (2026-06-18)
**Scope:** how `pytest-testcontainers` names the Docker containers it starts.

## Problem

In the default (non-reuse) mode the plugin never names its containers, so
Docker assigns random names (`heuristic_cannon`, `nifty_ganguly`). They are
impossible to identify in `docker ps`. Only *reuse mode* names containers
today, and even there the name (`<project>-tc-<service>-<worker>`) does not
encode which git branch or which working directory the container belongs to —
so two checkouts of the same project share a name and collide.

We want every container the plugin starts to carry a readable, identifiable
name derived from the project, the service, the **git branch**, and the
**directory**, with the scheme **configurable** via a template.

## Goals

- Name containers in **both** modes (default *and* reuse).
- Encode `(project, service, branch, directory, worker)` so containers are
  identifiable in `docker ps` and reuse is correctly scoped.
- Configurable through a **template string**.
- Honor external project-name env vars (`PROJECT_NAME`, `COMPOSE_PROJECT_NAME`).
- Respect Docker's 255-char container-name limit with a deterministic
  trim+checksum.

## Non-goals

- Changing the per-worker (xdist) container model — `scope="session"` still
  means per worker. Worker id stays in the name.
- Any single-shared-container behavior (that is a different product).

## Name scheme & template

Default template:

```
{project}-tc-{service}-{branch}-{dirtag}-{worker}
```

Example: `myproj-tc-psql-feature-x-3f9ac1b2-master`

### Placeholders

| Placeholder | Meaning |
|-------------|---------|
| `{project}` | Resolved project name (see precedence below). |
| `{service}` | Service slug (`psql`, `redis`, `mysql`, `mongo`, `rabbitmq`, or derived from the container class for `make_container`). |
| `{branch}`  | Current git branch from `.git/HEAD` (no subprocess). Detached HEAD → 12-char commit SHA. Not a git repo → resolves to empty. |
| `{dir}`     | Sanitized basename of the worktree root. Human-readable, **not** collision-proof (two dirs can share a basename). |
| `{dirtag}`  | 8-hex `blake2s` of the worktree-root **absolute path**. Collision-proof disambiguator for "same branch checked out in two directories". |
| `{worker}`  | xdist worker id (`master` when not under xdist). |
| `{rand}`    | Random token, 4 hex chars (`secrets.token_hex(2)`). **Empty in reuse mode**; auto-appended in default mode when the template doesn't already contain it. |

- Empty placeholders collapse cleanly: no `--`, no leading/trailing dashes.
- The literal `-tc-` marker is kept in the default template so containers are
  easy to find/prune (`docker ps | grep -- -tc-`).
- `{dirtag}` is the default disambiguator (safe). Users who know their
  directories differ can swap to `{dir}` for readability.

## Configurability

`name_template` precedence (CLI > env > pyproject > built-in default):

1. `--testcontainers-name-template=<tpl>`
2. `PYTEST_TESTCONTAINERS_NAME_TEMPLATE=<tpl>`
3. `pyproject.toml` → `[tool.pytest_testcontainers].name_template`
4. Built-in default (above)

An unknown placeholder (e.g. `{foo}`) raises **`NameTemplateError`**, a new
`PytestTestcontainersError` subclass whose message lists the valid
placeholders.

## Project-name precedence

The `{project}` component is resolved by this chain (highest first):

```
--testcontainers-project        (CLI flag)
PYTEST_TESTCONTAINERS_PROJECT    (plugin's own env var)
PROJECT_NAME                     (generic env var)
COMPOSE_PROJECT_NAME             (docker-compose's standard env var)
pyproject.toml [project].name
cwd directory basename           (fallback; triggers the one-shot advisory)
```

Plugin-namespaced sources stay on top (most explicit intent). The two generic
env vars sit above the file-based sources. The existing one-shot stderr
advisory still fires only when we fall through to the literal `pytest-tc`
fallback (i.e. nothing in the chain matched and there is no cwd basename).

## Two modes

### Reuse mode
Template expanded with `{rand}` empty → **fully deterministic, byte-stable**
across runs. Find-or-create now scopes reuse per
`(project, service, branch, dir, worker)`.

**Behavior change:** switching branch or worktree produces a *new* container;
the previous one lingers (stopped) until `--testcontainers-clean` prunes it.
This is intentional (per-branch / per-directory isolation) and must be
documented, including in a migration note — existing reused containers carry
the old `<project>-tc-<service>-<worker>` name and will not be found after
upgrade, so a fresh container is created and the old one lingers.

### Default mode
Same template, but a unique `{rand}` suffix is appended (automatically if the
template doesn't already include `{rand}`) → no collisions across overlapping
or leaked runs. **New behavior:** default-mode containers, previously unnamed,
now carry the readable name. No find-or-create — they remain ephemeral
(started, then stopped at teardown, with the existing atexit safety net).

## Length safety: `finalize_name`

Docker container names are capped at 255 chars (charset
`[a-zA-Z0-9][a-zA-Z0-9_.-]+`). Lift the existing trim+checksum from
`compose_name` into one helper that every name passes through (both modes,
after template expansion and — in default mode — after the `{rand}` suffix):

1. Sanitize the whole string (invalid chars → `-`, collapse `-` runs, strip
   leading non-alnum and trailing `-._`).
2. If `len > 255`: `name[:246] + "-" + blake2s(full_name, digest_size=4).hexdigest()`.

The checksum hashes the **full untruncated name**, not just the discarded
tail — any difference anywhere changes the hash, so two long names sharing the
same first 246 chars but differing only deep in the tail still get distinct
results. It is fully deterministic (no randomness), which is required for
reuse-mode stability. Digest is bumped 2→4 bytes (4→8 hex) because branch
names make overflow common.

## Code layout

Dependency direction preserved (`reuse`/`makers` → `_internal`; `errors` is a
leaf).

- **New** `_internal/git_identity.py` — pure, filesystem-read-only:
  - `worktree_root(start: Path) -> Path` — walk up to the dir holding `.git`
    (handles `.git` as a dir *and* as a file pointing to a worktree gitdir);
    falls back to `start` when there is no repo.
  - `current_branch(start: Path) -> str | None` — read `HEAD` from the
    resolved gitdir; `ref: refs/heads/<b>` → `<b>`; raw SHA → short SHA
    (detached); unreadable / bare → `None`.
- **`reuse.py`**:
  - Extend project resolution with `PROJECT_NAME` + `COMPOSE_PROJECT_NAME`.
  - Read `[tool.pytest_testcontainers].name_template` from pyproject (reuse the
    existing upward pyproject walk).
  - Replace `compose_name` / `reuse_name_for` internals with: resolve all
    components → expand template → `finalize_name`. `{rand}` empty for reuse,
    populated for default mode. The explicit `reuse_name=` maker override keeps
    its current verbatim (sanitized-only) behavior.
  - Add `finalize_name(name: str) -> str`.
  - Add CLI/state for the name template (with reset hooks — see Testing).
- **`makers.py`** (`_make_generic`): compute a name in default mode too and
  call `instance.with_name(name)` before start; keep find-or-create gated on
  `reuse_on`. Default mode keeps atexit registration + teardown stop.
- **`plugin.py`**: add `--testcontainers-name-template` option and wire it into
  reuse state (alongside the existing `--testcontainers-project`).
- **`errors.py`**: add `NameTemplateError`.

## Errors

- `NameTemplateError(PytestTestcontainersError)` — raised at name-resolution
  time for an unknown placeholder; message enumerates valid placeholders.
- `ReuseConflictError` — unchanged.

## Testing

Unit (no Docker, `not docker_required`):

- Template expansion for every placeholder, and the empty-placeholder collapse.
- Project precedence across the full chain (CLI / `PYTEST_TESTCONTAINERS_PROJECT`
  / `PROJECT_NAME` / `COMPOSE_PROJECT_NAME` / pyproject / cwd).
- `name_template` precedence (CLI / env / pyproject / default).
- Branch detection: attached ref, detached HEAD (short SHA), `.git`-file
  worktree, and no-repo (→ empty).
- `dirtag` stable for a path and distinct across paths.
- `finalize_name`: ≤255 always, deterministic, distinct for names differing
  only in the tail.
- Reuse name byte-stable across two calls; default-mode name carries `{rand}`,
  reuse-mode name does not.
- Unknown placeholder → `NameTemplateError`.

`docker_required` smoke:

- A default-mode container actually carries the templated name
  (`docker inspect`).
- Reuse identity differs across two branches.

Test-isolation fix (important): `_clean_state` / `reset_cli_state` must also
clear `PROJECT_NAME`, `COMPOSE_PROJECT_NAME`, and the new template CLI/env
state. These vars are **not** `PYTEST_TESTCONTAINERS*`-prefixed, so the
conftest's existing env sweep does not catch them and they would leak across
tests.

`filterwarnings = ["error", ...]` stays satisfied: advisories use the existing
stderr-write pattern, not `warnings.warn`.

## Docs

- **README**: `name_template`, placeholder table, both precedence chains, the
  per-branch / per-directory reuse behavior, and the lingering-container note
  (`--testcontainers-clean`).
- **SPEC.md**: behavior-defining entry for the template + finalize rules.
- **CLAUDE.md**: extend the configuration-precedence table with the new env
  vars and the `--testcontainers-name-template` flag.

## Backward compatibility

Pre-1.0 (v0.1.0). Two visible changes:

1. Default-mode containers are now named (previously random) — the desired fix.
2. Reuse-mode name format changes, so containers reused from a prior version
   are not found and a fresh one is created; the old one lingers until
   `--testcontainers-clean`. Document in the changelog and README migration
   note.
