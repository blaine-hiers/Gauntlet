# Golden Tasks

Real tasks live directly in this folder as `*.yaml` — one file per task, authored
against the actual framework content. `examples/` holds one template per category;
copy one up a level, rename it, and fill in real paths and answers.

Rules for a good golden task:
- It mirrors something a teammate actually asks Claude to do.
- Deterministic checks first (`file_exists`, `file_not_exists`, `file_contains`,
  `file_unchanged`); add a `judge` rubric only for answer/draft quality.
- The answer key or expected destination must be verified by a human once, at
  authoring time — that is what makes it "golden".
- Never put company-sensitive content in these YAMLs beyond what the framework
  itself already contains; seed documents go in `seed/` (also reviewed).
- Target 12–16 tasks, 3–4 per category.
