---
name: autoresearch-monitor
description: Find an existing Hypoloom research run and open its TUI, or return its complete terminal command. Works after restarting Claude Code; never starts or resumes research.
argument-hint: "[run-id] [--state-root /absolute/path] [--json] [--no-web]"
disable-model-invocation: true
user-invocable: true
---

# Hypoloom monitor

This is the discoverable shortcut for `/autoresearch monitor`. Use the same
existing observer and launcher. The user invocation authorizes opening a
monitor, not creating, resuming, cancelling or migrating a research run.

1. Read the inherited `HYPOLOOM_RUNTIME_FAMILY` without changing it. A nonempty
   value other than `hypoloom` stops this operation. Resolve this repository
   with Git and its qualified `.conda/envs/hypoloom/bin/python`; preserve a
   previously supplied qualified interpreter. Do not read private Profiles,
   model settings or transcripts to discover runs.
2. Parse the invocation arguments as an optional exact run ID plus
   `--state-root`, `--interval`, `--web-port`, `--no-web` or `--json`.
   Run the tracked read-only helper once, using absolute paths:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 HYPOLOOM_RUNTIME_FAMILY=hypoloom \
   "$CONTROL_PYTHON" -B -m hypoloom.monitor_launch --inspect \
     --python "$CONTROL_PYTHON" --working-directory "$REPOSITORY"
   ```

   Translate the supplied positional ID to `--run-id`. Forward the other
   supplied options except `--json`: inspection already returns JSON;
   `--json` selects the observer command in step 4. Quote each argument normally,
   never eval or execute the raw user argument string.
3. Handle the returned result:
   - `ready`: show the exact selected run and state root, then continue.
   - `needs_selection`: show run ID, state root and **durable** state; ask the
     user which run to view. More than one match is not an implicit "latest"
     selection. Supply both the chosen root and ID on the next inspection.
     Use returned `next_offset` with `--offset` to inspect another page.
   - `empty` / `not_found` / `unavailable`: explain the result and warnings.
     An external root requires its explicit `--state-root`; do not search the
     filesystem, create a root or start a Host. An incomplete scan cannot pick
     a run automatically.
   Discovery reads existing `state/*/state.sqlite` only and does not probe Host
   liveness. A recorded ACTIVE state is not evidence that research is running.
4. For a `ready` result, execute the returned `commands.launcher_argv` with the
   selected repository as cwd and `PYTHONDONTWRITEBYTECODE=1
   HYPOLOOM_RUNTIME_FAMILY=hypoloom`. Inside tmux it opens the existing TUI in a
   new window. Without tmux, it launches nothing: return the one complete
   `commands.terminal_command` for an external interactive terminal. Never run
   the Textual interface inside CC's captured Bash output.
   If the user requested `--json`, execute `commands.json_argv` instead and
   relay the existing observer JSON unchanged, without opening a TUI or Web port.
5. Include `commands.cc_command` so the exact selection is easy to reuse.
   `q` leaves only the monitor; the Host and research keep their existing state.
   After CC restarts, `/autoresearch-monitor` reconstructs choices from the same
   stores. Native slash completion covers the skill name, not dynamic run IDs;
   do not promise gray prompt suggestions or modify CC input history/settings.

Web ports/tokens are created by the observer when it opens. Do not invent or
persist an access URL. Further observer details are in the existing
[operations reference](../autoresearch/references/operations.md#3-status-monitor-and-oracle).
