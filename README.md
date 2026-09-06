# Gauntlet

A benchmark harness for context engineering. It answers one question:

> **Is this CLAUDE.md helping or hurting the model?**

Gauntlet A/B-tests golden tasks against a frozen snapshot of a knowledge base
under multiple CLAUDE.md variants, then reports which sections earn their
tokens. It grades on **outcome quality**, not on obedience: a task passes
because the model produced the right result, not because it followed a
prescribed set of steps.

A worked example ships with it, so the harness runs end to end on a clean
clone with no setup beyond installing dependencies.

## Why it exists

Context files accumulate. Sections get added after a bad answer and never
removed, and nobody can say which ones are still doing work. Gauntlet makes that
measurable: freeze the corpus, swap the context file, run the same tasks, and
compare.

## Setup

```bash
pip install -r requirements.txt
```

`gauntlet.config.json` ships pointed at the bundled `corpus/`, so the commands
below work immediately. Point `synced_root` at your own knowledge base when
you want real numbers.

## Usage

```bash
python -m gauntlet.cli snapshot                  # freeze the corpus into data/snapshot
python -m gauntlet.cli lint                      # static findings on the snapshot's CLAUDE.md
python -m gauntlet.cli run --label baseline      # run every task against every variant
python -m gauntlet.cli report --label baseline   # scored markdown report
```

Run it again under a new label whenever a new model ships or the context file
changes materially, then diff the reports.

Every task × variant cell is one sample of a stochastic process, so a single
run cannot tell a real gap from noise. `--repeats N` runs each cell N times;
the report then shows each cell as mean ± sd and only flags a task when the
`empty` variant beats `current` by more than the standard error of the
difference. Re-running a label with a higher `--repeats` tops the cells up
rather than starting over.

```bash
python -m gauntlet.cli run --label baseline --repeats 5
```

## How a task is scored

Each golden task carries two independent gates, and both are reported:

- **Deterministic checks** on the filesystem after the run: `file_exists`,
  `file_not_exists`, `file_contains`, `file_unchanged`. These cannot be argued
  with. `file_unchanged` is how a read-only task proves it stayed read-only.
- **An LLM judge**, given a rubric and an answer key, scoring 0 to 10. The
  rubric names the specific traps, so a plausible-sounding wrong answer scores
  badly rather than passing on tone.

A task needs at least one of the two. Four categories are valid: `find-answer`,
`file-organize`, `draft-deliverable`, `framework-upkeep`.

```yaml
id: fa-dc-count
category: find-answer
prompt: "How many distribution centers does Meridian Tools operate today?"
checks:
  - type: file_unchanged
    path: "QUICK ANSWERS - Meridian Fact Index.md"
judge:
  rubric: "Must state THREE and name them. Must NOT claim a Jacksonville DC exists."
  answer_key: "Ashgrove TN, Baytown TX, Cordell OK."
```

`tasks/examples/` holds one skeleton per category. `tasks/README.md` covers
authoring in full.

## Variants

`variants` in the config maps a name to a CLAUDE.md replacement:

| Value | Means |
|---|---|
| `null` | the snapshot's own CLAUDE.md, unmodified |
| `""` | no CLAUDE.md at all, the floor to measure against |
| a path | that file, substituted in |

The `empty` variant is the one that matters. If a context file cannot beat
having no context file, it is costing tokens for nothing.

## Contamination isolation

Context leaks into a run from two directions, and the harness closes both.

**From the directory tree.** Claude Code loads `CLAUDE.md` (and
`.claude/CLAUDE.md`) from the working directory and every ancestor up to the
filesystem root. Runs therefore execute under the system temp root, never
inside this repository, so they cannot inherit the project's own context. But
the walk does not stop at the temp root: on Windows the temp dir sits under
the user's home, which puts `~/.claude/CLAUDE.md` on the path as if it were
project context. Before a run starts, the harness checks every ancestor of the
run directory and **refuses to run** if any holds a context file — set
`work_root` in the config to a directory outside your home when it does. The
directory used is recorded on every row as `work_root`.

**From the user's settings.** Claude Code also loads the invoking user's
`~/.claude` settings — user memory, enabled plugins, skills, and MCP servers —
regardless of the working directory. The runner passes
`--setting-sources project,local` so user-scope settings and memory are not
read, and `--strict-mcp-config` so no MCP server loads at all. The flags are
recorded on every row as `isolation`.

Both were verified against claude 2.1.261 with marker files: a run under the
flags reads the run directory's own `CLAUDE.md` and nothing from `~/.claude`,
*provided* the run directory has no ancestor holding one. These are the
details that make the numbers trustworthy, and they are easy to get wrong by
accident.

## Layout

```
gauntlet/          harness: cli, snapshot, runner, judge, scoring, lint, report
tasks/          golden tasks, plus examples/ skeletons per category
corpus/         the bundled demo knowledge base under test
variants/       CLAUDE.md replacements for A/B runs
data/           snapshots and run output (gitignored)
tests/          unit tests
```

## Tests

```bash
python -m pytest
```
