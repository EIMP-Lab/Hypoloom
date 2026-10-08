# Hypoloom research runtime

Use the canonical `/autoresearch` skill for research operations. It targets the
independent `hypoloom` runtime and its existing CLI. Start with `/autoresearch
init` for a fresh topic; init is Claude orchestration, not a CLI subcommand.
Use `/autoresearch-monitor` to choose an existing run and open its observer,
including after restarting CC. `/autoresearch monitor` remains supported.
After successful init, show the generated CC and full terminal monitor commands.

Research sessions do not edit Hypoloom framework source, dependencies, Git metadata
or CC controls. The project hook restricts file writes and wraps Bash/Monitor in
bubblewrap with read-only source; only `.local/`, `profiles/local/` and `state/`
are writable mounts. Direct file-tool edits of state remain prohibited. If isolation
cannot start, report the prerequisite failure; never retry outside it or change hooks.
Framework repairs belong in a separate authorized development session.

The default control interpreter is `$PWD/.conda/envs/hypoloom/bin/python` with
SQLite at least 3.51.3. GPU/experiment Python is a separately reviewed runtime.
An absent `HYPOLOOM_RUNTIME_FAMILY` assertion is allowed. A nonempty value other
than exactly `hypoloom` stops every operation. Init and managed start complete
in the same Claude interaction. Every helper launched by the skill explicitly
receives `HYPOLOOM_RUNTIME_FAMILY=hypoloom` and its reviewed paths.

Public v4 Profiles are in `profiles/templates/`, with English and Chinese
multi-expert variants. Real topics belong in ignored `profiles/local/`.
Never overwrite an existing Profile or mutate a retained run during init.
Review exact target/data paths, model authentication, budgets, GPU UUIDs,
literature access and notification recipients before one final Start now or
Prepare only choice. Reuse the tracked model/Host/monitor helpers.

Init uses `python -B -m hypoloom.bootstrap`: `paths` displays private/default
locations without values, `prepare` validates filled template contents in memory
(`--write` exclusively creates a new Profile), and `launch` derives all arguments
(`--start` performs the authorized managed sequence). Read-only previews create
no Host or state. The public credential template is `config/hypoloom.env.example`;
the private default remains `.local/credentials/hypoloom.env`. Model auth is separate.

Inspect generates `interaction.questions` in `--ui-language en|zh-CN`. Relay them
unchanged, save the original answers under `.local/`, and use `answer_argv` to
translate them into the existing CLI choices. Do not redesign the question pages,
invent publisher batches, or interpret these answers as the final launch approval.

Existing runs remain read-only without explicit operation authorization.
Their frozen runtime determines their execution identity. Never migrate a run
to the working tree or write SQLite directly. An ACTIVE record alone does not
prove a Host is alive. A timed-out control reply must be reconciled using the
same request identity. Unknown charges are not zero.

The Host owns durable state, experiment execution, resource grants, native
session containment and accepted publication. Agents use private chain Git
worktrees and scoped tools; original target/data roots are read-only. Grounding
may ask real human questions. Common acceptance, when selected, must be closed
by one complete GOAL candidate, independently checked by Oracle.

TUI q leaves monitoring and keeps the Host running. End run confirms cancellation
and waits for cleanup. Do not kill a service merely because a monitor closed.
External notifications and private support exports require their explicit scope.
No secrets, settings files, research data or model transcripts go into Git.
Research-runtime session transcripts for this project live in ignored
`.local/claude/projects/-data-Gray-Project-Hypoloom/` (symlinked back from
`~/.claude/projects/`); they are local evidence, like run state and
`.local/credentials/`.

See `docs/qualification.md` for current validation scope and promotion rules,
`SETUP.md` for prerequisites, and `USAGE.md` for operating commands. Development
memory in `docs/codex-memory/` belongs to Codex maintenance and is not runtime
research memory.
