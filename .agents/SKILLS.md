# Project skills

<!-- generated-by: ageroot; template: <template-version>; commit: <short-git-commit>; rendered-at: <ISO-8601 timestamp> -->

Project skills are managed with [skills-manager](https://github.com/xingkongliang/skills-manager).
It creates symlinks from this project to shared skill store at `~/.skills-manager`.

## Operating rules

- Use `skills-manager` to install, update, and remove project skills.
- Do not copy skill directories into this repository or edit linked skill contents here.
- Read this file only when configuring or updating skills; it is not required at normal agent startup.
- Keep this list aligned with project needs. Categorization is policy, not an installed-state report.
- Required skills may be installed globally. A project symlink is needed only when
  the project must pin or otherwise manage that skill locally.

## Required

- `caveman` — concise communication protocol.
- `rtk` — token-efficient shell command output.

## Recommended

- `caveman-commit` — concise Conventional Commit messages.
- `caveman-explore` — read-only repository orientation and cross-file discovery.
- `caveman-review` — compact code-review findings.
- `planning-with-files` — durable planning for multi-step implementation work.
- `memory-manager` — episodic reflection, append-only journal writes, candidate review, semantic rendering, FTS5 search, and migration.
- `memory-maintenance` — offline clustering, candidate staging, decay/archival, review queue upkeep, and FTS rebuilds (depends on `memory-manager`).

## Optional

### [mattpocock/skills](https://github.com/mattpocock/skills)

Engineering and workflow skills for disciplined agentic development:

- `setup-matt-pocock-skills` — configure repo for engineering skills (issue tracker, triage labels, domain docs).
- `ask-matt` — route to appropriate skill or flow for current situation.
- `grill-me` — relentless interview to stress-test and sharpen plans or designs.
- `grill-with-docs` — design interview that updates domain model, `CONTEXT.md`, and ADRs.
- `to-spec` — synthesize conversation into formal issue spec.
- `to-tickets` — decompose plans/specs into dependency-linked tracer-bullet tickets.
- `implement` — execute spec/tickets driven by TDD and reviewed before commit.
- `tdd` — test-driven development loop (red-green-refactor).
- `diagnosing-bugs` — structured hypothesis-driven bug and regression diagnosis loop.
- `code-review` — parallel sub-agent review for standards compliance and spec fidelity.
- `codebase-design` — shared vocabulary and patterns for deep module boundaries.
- `improve-codebase-architecture` — scan codebase for architectural deepening opportunities.
- `domain-modeling` — sharpen ubiquitous language and maintain project domain context.
- `resolving-merge-conflicts` — intent-based hunk-by-hunk git conflict resolution.
- `prototype` — build throwaway prototypes to validate logic or UI.
- `research` — investigate questions against primary sources into cited Markdown.
- `triage` — state-machine issue and PR triage workflow.
- `wayfinder` — multi-session project planning via decision ticket maps.
- `wait-what` — re-pitch unclear or mismatched communication.
- `handoff` — compact session context into a structured handoff document.
- `wizard` — interactive bash wizard for human-only operational steps.
- `teach` — stateful instructional workspace for interactive learning.
- `to-questionnaire` — convert open decisions into targeted stakeholder questionnaires.
