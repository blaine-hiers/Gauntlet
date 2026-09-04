# CLAUDE.md

Context for working inside the Meridian Tools knowledge base. Meridian is a
fictional industrial distributor; this corpus exists so the harness has a
framework to benchmark against.

## Layout

- `QUICK ANSWERS - Meridian Fact Index.md` is the fact registry. It wins over
  any other file when two sources disagree.
- `reference/` holds durable how-to and definition documents.
- `inbox/` holds unfiled captures. Nothing stays here.
- `projects/` holds one folder per active project, with `_index.md` listing them.

## Rules

- ALWAYS read the fact index before answering a question about the business.
- NEVER invent a fact that is not in the corpus. If it is not recorded, say so.
- A capture in `inbox/` is filed by moving it, not by copying it.
- When you add a project folder, you MUST add its row to `projects/_index.md`.
