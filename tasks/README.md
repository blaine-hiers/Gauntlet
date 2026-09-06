# Golden Tasks

Real tasks live directly in this folder as `*.yaml` — one file per task, authored
against the actual framework content. `examples/` holds one template per category;
copy one up a level, rename it, and fill in real paths and answers.

Rules for a good golden task:
- It mirrors something a teammate actually asks Claude to do.
- Deterministic checks first (see below); add a `judge` rubric only for
  answer/draft quality that no check can express.
- The answer key or expected destination must be verified by a human once, at
  authoring time — that is what makes it "golden".
- Never put company-sensitive content in these YAMLs beyond what the framework
  itself already contains; seed documents go in `seed/` (also reviewed).
- Target 12–16 tasks, 3–4 per category.

## Check types

Every check but `snapshot_unchanged` takes a `path`. `path: "$response"` targets
the model's own answer text instead of a file on disk — the way to assert a
trap deterministically (e.g. "must not say Jacksonville") without leaning on
the judge for something a plain text match can catch.

| Type | Fields | Passes when |
|---|---|---|
| `file_exists` | `path` | The file exists. |
| `file_not_exists` | `path` | The file does not exist. |
| `file_contains` | `path`, `text` | The file exists and contains `text` (case-insensitive). |
| `file_not_contains` | `path`, `text` | The file/response is missing, or exists without `text` (case-insensitive). |
| `file_matches` | `path`, `pattern` | The file exists and `pattern` (a regex) is found in it. |
| `file_unchanged` | `path` | The file's hash still matches the snapshot manifest. |
| `snapshot_unchanged` | `allow` (optional glob list) | No file under the run dir was added, removed, or modified relative to the manifest, except paths matching an `allow` glob. This is the blast-radius check: a `file_unchanged` on one path proves nothing about the other forty. Always `allow` the CLAUDE.md variant swap (`CLAUDE.md`) and any `setup`-copied paths, since those are intentional, expected differences from the frozen snapshot. |

A failed `snapshot_unchanged` result carries `drifted_count` and `drifted` (the
list of relative paths that differ), not just pass/fail, so a report reader can
see exactly what a task trashed.
