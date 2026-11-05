# 0031: Separate bilingual retrieval learning workspace

Date: 2026-10-13

## Context

The retrieval exercise was prepared outside the application repository. George wants
an ordinary VS Code project, a branch suitable for a future push, and control of the
learning process without changing the main checkout or his course environment.

## Options

Keep the unrelated exercise folder, work directly in the main checkout, or create a
separate Git worktree belonging to the existing repository.

## Decision

Use `C:\Users\george\Desktop\Projects\bilingual-retrieval` as a worktree on
`experiment/bilingual-retrieval`, starting from local main at `3a9bd69`.
Copy the exercise with its relative paths intact and retain the original as a backup.
Track code and setup documentation; ignore evidence, judgments, generated outputs,
model files, and virtual environments pending a deliberate publication decision.

## Consequences

The main checkout and frontend are unchanged. The branch can be reviewed and pushed
later, but this setup creates no commit or push. Git does not back up ignored data.
The old virtual environment is not relocated because environments contain path-specific
files. Dependency installation, model downloads, and baseline execution remain separate
learning steps requiring agreement. The local review server now uses the copied data.

## 2026-10-17 organization update

George found the chronological folders difficult to navigate. Organize the exercise
by purpose: data/articles, data/labels, data/prepared, scripts, review, results, archive.
Retain first_retrieval.py at the top level for his own learning code. Preserve source
and review packet bytes, including the packet hash used by the append-only journal.
Resolve existing review record paths against data/articles in the tools. Archive the
previous generated outputs and regenerate prepared passages and lexical rankings with
new paths. Record the relocation hashes in the ignored archive directory. No model
installation, download, or training is part of this reorganization.

## 2026-11-05 cross-computer transfer

George explicitly requested pushing the current files to resume on another computer.
Include the small article sample, human review journal, prepared data, current rankings,
and historical archive in the experiment branch. This supersedes the initial local-only
data decision. Keep credentials, runtimes, models, caches, logs, and the personal guide
excluded. Mark data/archive/results as -text in .gitattributes so Git preserves exact
bytes across operating systems. Add a portable handoff and read-only integrity check.
This transfer does not authorize training, a main merge, a deployment, or future pushes.
