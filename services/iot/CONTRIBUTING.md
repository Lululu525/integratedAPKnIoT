# Contributing

A secure OTA update system for ESP32 devices. See `README.md` for setup and for
running the server, dashboard, and device.

## How work is tracked

Everything lives in GitHub. There is no planning document to keep in sync,
because a document costs a pull request to update and drifts out of date.

- **Milestones** M1 to M6 hold the plan: each one's description says what it
  covers, what is already done, and what "done" means for the milestone as a
  whole. They are ordered, and the order is deliberate.
- **Issues** hold individual work items. Each one states what breaks or what is
  missing, the evidence, and a "Done when" list a reviewer can check against.
- Dependencies between pieces of work are written in the issues themselves, as
  "Blocks" and "Blocked by" lines, so an issue is readable on its own.
- An issue with no milestone is a proposal nobody has accepted yet. Do not start
  one without agreeing it first.

Issues labelled `good first issue` are scoped so someone new to the codebase can
finish them without needing the whole picture.

## Guiding rules

These decide arguments about scope. They are not aspirations.

- Ship a thin, sellable MVP first. Everything that can wait, waits.
- Do not rewrite signing or verification from scratch. The crypto and OTA logic
  is ported from a working prototype and is byte-for-byte compatible with
  firmware already in the field.
- SQLite and local disk are the starting point. Persistence and storage sit
  behind small interfaces so they can be swapped later without touching feature
  code. Nothing else is abstracted, on purpose.
- The security-critical path (keys, signing, verification) is owned by the
  project maintainer. Other work comes first for anyone new.
- Docker is not a development requirement. It arrives at the deployment
  milestone and not before.

## Branches, commits, pull requests

- One feature branch per change, named after the kind of change: `feat/...`,
  `fix/...`, `docs/...`, `chore/...`.
- Small commits. Roughly a hundred lines of related change, not a week of work
  squashed into one message with a list of bullet points.
- Every change goes through a pull request. `main` is protected.
- Pull request titles use `<tag>: description`. Commit messages are plain
  descriptive sentences with no tag prefix, sentence case, no trailing period.
- A pull request body says what broke, how it was fixed, and what was verified.
  It does not explain why the problem was missed originally or what the fix
  means for the project.

## Quality gates

CI runs on every pull request and a failing one cannot merge. Run the same
checks locally first:

```bash
uv run pre-commit run --all-files   # black, ruff, clang-format, editorconfig
uv run pytest                       # backend unit tests
```

Python is managed exclusively with `uv`. Never call `pip` or a bare `python`.

`black` and `ruff` are configured for line length 100. `clang-format` covers
`esp32/`. The frontend has no Prettier and no stylistic ESLint rules: the
2-space convention comes from `.editorconfig` at the repo root, which needs the
EditorConfig extension in VS Code to take effect.

## Writing code and comments

Comments are English, plain, and short. No numbered lists, no ASCII decoration.

Comment the choice a reader would otherwise stop and question, not the change
that introduced it. A comment explains what a line means to the system as it
stands now, not what was wrong before it was written. Describe the repository as
it is, not the task that produced the code.

Before publishing, read each comment back and delete it if the line already says
it, or if another comment or the commit message already said it. The default is
no comment; earn each one.

## Where the deeper detail lives

`README.md` covers setup, key generation, flashing a device, and publishing an
update. The signing contract between server and device, the backend layering
rules, and the device protocol are documented alongside the code they describe.
