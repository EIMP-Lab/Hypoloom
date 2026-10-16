# Architecture overview

English · [简体中文](architecture.zh-CN.md)

Hypoloom coordinates research through a persistent Workflow Host. Experts propose
hypotheses and experiments; the Host schedules approved work and records results.
An independent Oracle verifies recorded evidence and execution consistency.

## From a goal to evidence

1. **Configure:** a six-file Profile supplies the goal, experts, methodology,
   language, notifications and safety limits. Initialization reviews the target
   repository, data, model and resource permissions before a new run starts.
2. **Ground:** literature and human answers clarify the goal. When common
   acceptance is selected, Grounding supplies the shared evaluator and validity
   checks used to assess experiments.
3. **Research:** experts read papers, propose hypotheses, design implementations,
   request experiments, analyze results and update their research direction.
   Debates, a selected leader and skill evolution support collaboration.
4. **Execute:** the Host validates the submitted design and launches a bounded
   Process in the admitted environment. The candidate code, raw outputs and
   measurement parser have explicit inputs and ownership.
5. **Assess:** accepted measurements are linked to their hypothesis and experiment.
   Scientific assessment and goal evaluation use that evidence; the final report
   records results, limitations and incomplete work.

See [research workflow](guides/research-workflow.md) and
[reading and proposals](guides/reading-and-proposals.md) for each research stage.

## Component responsibilities

| Component | Responsibility |
| --- | --- |
| Workflow Host | Durable state, request validation, scheduling, collection and result publication |
| Research Agent | Scientific proposals, candidate code and scoped tool use |
| Experiment Process | Actual CPU/GPU computation under the admitted limits |
| GPU pool | Resource commitments and capacity arbitration for participating runs |
| Oracle | Independent validation of stored identities, outcomes, evidence and lifecycle consistency |
| Web and terminal observers | Read-only projections plus explicit operator action controls |
| Operator | Goals, credentials, budgets, device authorization, human answers and stopping |

Agents use private workspaces and scoped tools. The original target repository
and admitted data roots are read-only during research. File and command access
does not permit editing the framework, expanding resource authorization or
writing the run database directly. The Host remains the writer of accepted state.

## State and results

The run state root contains its SQLite records, artifacts and runtime bundle.
Commands and Attempts identify who requested and executed work; accepted research
facts connect goals, hypotheses, experiments, evidence and assessments. Logs
help diagnose execution, while accepted records and their artifacts establish
what the run actually produced.

An `ACTIVE` record alone does not prove the Host is alive. Read the current Host
observation separately. Likewise, `COMPLETED` does not by itself prove the
scientific goal was met: inspect the goal verdict, accepted measurements and
final report. Partial LOCAL results cannot be combined to invent a successful
common GOAL candidate.

## Resources and budgets

CPU and GPU experiments use the environments and paths reviewed for the run.
GPU access requires exact device authorization. A shared pool accounts for
grants; measured workload memory can inform later resource requests. Other
users' GPU activity remains outside the pool's commitments, so live capacity
and pressure checks still apply. See [GPU resources](guides/gpu-resource-planning.md).

Run, expert, Agent and Process limits bound different scopes. A long experiment
timeout does not extend an Agent's deadline. Reported usage, open reservations
and unknown charges remain distinguishable; an estimate is not an upstream bill.

## Observation and recovery

Web and terminal interfaces read the same durable research records. Human
answers, provider changes and End run are explicit commands; closing an observer
does not stop research. End run requests cancellation and waits for owned work
to close. See [Usage](../USAGE.md) and [API recovery](guides/provider-recovery.md).

Each run keeps its original runtime bundle and admitted configuration. Updating
the checkout does not upgrade an existing run, and cross-version run migration
is not supported. Do not edit SQLite to repair a run. Use the documented
[runtime recovery](guides/runtime-recovery.md) and backup procedures.
