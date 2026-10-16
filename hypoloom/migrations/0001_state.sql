BEGIN IMMEDIATE;
CREATE TABLE schema_migrations (
 version INTEGER PRIMARY KEY CHECK(version>0),
 applied_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE TABLE workflow_runs (
 run_id TEXT PRIMARY KEY, derived_from_run_id TEXT,
 workflow_definition_version TEXT NOT NULL, research_contract_version TEXT NOT NULL,
 evaluator_version TEXT NOT NULL, agent_aci_version TEXT NOT NULL, runner_version TEXT NOT NULL,
 artifact_result_schema_version TEXT NOT NULL, runtime_environment_id TEXT NOT NULL,
 configuration_id TEXT NOT NULL, sqlite_runtime_version TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 CHECK(run_id<>''), CHECK(derived_from_run_id IS NULL OR derived_from_run_id<>run_id)
) STRICT;
CREATE TABLE workflow_commands (
 command_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
 workflow_id TEXT NOT NULL, expected_revision INTEGER, command_type TEXT NOT NULL,
 request_json TEXT NOT NULL CHECK(json_valid(request_json,1)),
 response_status TEXT CHECK(response_status IS NULL OR response_status IN('accepted','rejected','error')),
 result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json,1)),
 error_json TEXT CHECK(error_json IS NULL OR json_valid(error_json,1)), accepted_revision INTEGER,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 CHECK(command_id<>'' AND workflow_id<>'' AND command_type<>''),
 CHECK(expected_revision IS NULL OR expected_revision>=0), CHECK(accepted_revision IS NULL OR accepted_revision>=0),
 CHECK(response_status IS NULL OR (response_status='accepted' AND result_json IS NOT NULL AND error_json IS NULL) OR (response_status IN('rejected','error') AND result_json IS NULL AND error_json IS NOT NULL))
) STRICT;
CREATE TABLE research_facts (
 fact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
 fact_type TEXT NOT NULL CHECK(fact_type IN('GoalDefined','HypothesisProposed','ExperimentPreregistered','CanonicalOutcomeAccepted','EvidenceLinked','HypothesisAssessed','GoalVerdictRecorded')),
 schema_version INTEGER NOT NULL CHECK(schema_version>0), subject_id TEXT NOT NULL,
 goal_id TEXT, hypothesis_id TEXT, experiment_id TEXT, execution_id TEXT, outcome_id TEXT,
 artifact_id TEXT REFERENCES execution_artifacts(artifact_id) DEFERRABLE INITIALLY DEFERRED,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json,1)),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 CHECK(fact_id<>'' AND subject_id<>''),
 CHECK(fact_type<>'CanonicalOutcomeAccepted' OR json_extract(payload_json,'$.outcome') IN('SUCCESS','CLEAN_REFUTATION','INCONCLUSIVE','EXECUTION_FAILURE','INTEGRITY_REJECTED','CANCELLED')),
 CHECK((fact_type='GoalDefined' AND goal_id IS NOT NULL) OR
       (fact_type='HypothesisProposed' AND goal_id IS NOT NULL AND hypothesis_id IS NOT NULL) OR
       (fact_type='ExperimentPreregistered' AND hypothesis_id IS NOT NULL AND experiment_id IS NOT NULL AND execution_id IS NOT NULL) OR
       (fact_type='CanonicalOutcomeAccepted' AND experiment_id IS NOT NULL AND execution_id IS NOT NULL AND outcome_id IS NOT NULL AND artifact_id IS NOT NULL) OR
       (fact_type='EvidenceLinked' AND hypothesis_id IS NOT NULL AND outcome_id IS NOT NULL AND artifact_id IS NOT NULL) OR
       (fact_type='HypothesisAssessed' AND hypothesis_id IS NOT NULL) OR
       (fact_type='GoalVerdictRecorded' AND goal_id IS NOT NULL))
) STRICT;
CREATE TABLE research_fact_refs (
 fact_id TEXT NOT NULL REFERENCES research_facts(fact_id) DEFERRABLE INITIALLY DEFERRED,
 ref_fact_id TEXT NOT NULL REFERENCES research_facts(fact_id) DEFERRABLE INITIALLY DEFERRED,
 role TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0 CHECK(ordinal>=0),
 PRIMARY KEY(fact_id,role,ordinal), UNIQUE(fact_id,ref_fact_id,role),
 CHECK(fact_id<>ref_fact_id AND role<>'')
) STRICT;
CREATE TABLE workflow_transitions (
 workflow_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=0),
 run_id TEXT NOT NULL REFERENCES workflow_runs(run_id), workflow_kind TEXT NOT NULL CHECK(workflow_kind IN('RUN','EXPERIMENT')),
 previous_state TEXT, state TEXT NOT NULL,
 command_id TEXT NOT NULL REFERENCES workflow_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
 next_command TEXT, due_at REAL,
 fact_id TEXT REFERENCES research_facts(fact_id) DEFERRABLE INITIALLY DEFERRED,
 payload_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload_json,1)),
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(workflow_id,revision), UNIQUE(workflow_id,command_id), CHECK(workflow_id<>''),
 CHECK(due_at IS NULL OR next_command IS NOT NULL),
 CHECK((workflow_kind='RUN' AND state IN('CREATED','ACTIVE','WAITING_HUMAN','COMPLETING','STOPPING','COMPLETED','FAILED','CANCELLED')) OR
       (workflow_kind='EXPERIMENT' AND state IN('DRAFTED','PREREGISTERED','READY','EXECUTING','EVALUATING','TERMINAL')))
) STRICT;
CREATE TABLE workflow_human_decisions (
 decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES workflow_runs(run_id), workflow_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('OPEN','RESOLVED')),
 family TEXT NOT NULL CHECK(family IN('GOAL_SCOPE','BUDGET_RESOURCE','IRREVERSIBLE_SAFETY')),
 request_revision INTEGER NOT NULL CHECK(request_revision>=0), request_json TEXT NOT NULL CHECK(json_valid(request_json,1)), deadline REAL,
 resolution TEXT CHECK(resolution IS NULL OR resolution IN('APPROVED','REJECTED','EDITED','TIMED_OUT','CANCELLED')),
 resolution_json TEXT CHECK(resolution_json IS NULL OR json_valid(resolution_json,1)), resolved_at REAL,
 command_id TEXT NOT NULL REFERENCES workflow_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
 CHECK((state='OPEN' AND resolution IS NULL AND resolution_json IS NULL AND resolved_at IS NULL) OR
       (state='RESOLVED' AND resolution IS NOT NULL AND resolution_json IS NOT NULL AND resolved_at IS NOT NULL))
) STRICT;
CREATE TABLE execution_attempts (
 attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES workflow_runs(run_id), workflow_id TEXT NOT NULL,
 command_id TEXT NOT NULL REFERENCES workflow_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
 kind TEXT NOT NULL CHECK(kind IN('AGENT','PROCESS')), experiment_id TEXT, execution_id TEXT,
 attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal>0), host_generation TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('OPEN','CLOSED')), started_at REAL NOT NULL, ended_at REAL,
 terminal_reason TEXT CHECK(terminal_reason IS NULL OR terminal_reason IN('SUCCEEDED','FAILED','TIMED_OUT','CANCELLED','LOST')),
 process_group_id INTEGER CHECK(process_group_id IS NULL OR process_group_id>0),
 observation_json TEXT CHECK(observation_json IS NULL OR json_valid(observation_json,1)),
 CHECK(attempt_id<>'' AND workflow_id<>'' AND host_generation<>''),
 CHECK((kind='AGENT' AND experiment_id IS NULL AND execution_id IS NULL AND process_group_id IS NULL) OR (kind='PROCESS' AND experiment_id IS NOT NULL AND execution_id IS NOT NULL)),
 CHECK((state='OPEN' AND ended_at IS NULL AND terminal_reason IS NULL AND observation_json IS NULL) OR
       (state='CLOSED' AND ended_at IS NOT NULL AND terminal_reason IS NOT NULL AND observation_json IS NOT NULL))
) STRICT;
CREATE TABLE execution_artifacts (
 artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES workflow_runs(run_id),
 experiment_id TEXT NOT NULL, execution_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL REFERENCES execution_attempts(attempt_id), artifact_kind TEXT NOT NULL CHECK(artifact_kind='result'),
 schema_version TEXT NOT NULL, digest_algorithm TEXT NOT NULL CHECK(digest_algorithm='sha256'),
 digest TEXT NOT NULL CHECK(length(digest)=64 AND digest NOT GLOB '*[^0-9a-f]*'), path TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 UNIQUE(run_id,experiment_id,execution_id,attempt_id,artifact_kind), UNIQUE(path), CHECK(artifact_id<>'' AND path<>'')
) STRICT;
CREATE UNIQUE INDEX research_subject_identity ON research_facts(run_id,fact_type,subject_id);
CREATE UNIQUE INDEX research_one_goal_per_run ON research_facts(run_id) WHERE fact_type='GoalDefined';
CREATE UNIQUE INDEX research_one_verdict_per_run ON research_facts(run_id) WHERE fact_type='GoalVerdictRecorded';
CREATE UNIQUE INDEX research_one_experiment_execution ON research_facts(run_id,execution_id) WHERE fact_type='ExperimentPreregistered';
CREATE UNIQUE INDEX research_one_canonical_outcome ON research_facts(run_id,experiment_id,execution_id) WHERE fact_type='CanonicalOutcomeAccepted';
CREATE UNIQUE INDEX execution_agent_attempt_ordinal ON execution_attempts(workflow_id,command_id,attempt_ordinal) WHERE kind='AGENT' AND execution_id IS NULL;
CREATE UNIQUE INDEX execution_process_attempt_ordinal ON execution_attempts(workflow_id,command_id,execution_id,attempt_ordinal) WHERE kind='PROCESS' AND execution_id IS NOT NULL;
CREATE UNIQUE INDEX execution_one_open_agent_attempt ON execution_attempts(workflow_id,command_id) WHERE kind='AGENT' AND state='OPEN' AND execution_id IS NULL;
CREATE UNIQUE INDEX execution_one_open_process_attempt ON execution_attempts(workflow_id,command_id,execution_id) WHERE kind='PROCESS' AND state='OPEN' AND execution_id IS NOT NULL;
CREATE UNIQUE INDEX workflow_one_open_decision ON workflow_human_decisions(workflow_id) WHERE state='OPEN';
CREATE TRIGGER immutable_workflow_runs_update BEFORE UPDATE ON workflow_runs BEGIN SELECT RAISE(ABORT,'workflow run versions are immutable'); END;
CREATE TRIGGER immutable_workflow_runs_delete BEFORE DELETE ON workflow_runs BEGIN SELECT RAISE(ABORT,'workflow run versions are immutable'); END;
CREATE TRIGGER immutable_workflow_commands_update BEFORE UPDATE ON workflow_commands BEGIN SELECT RAISE(ABORT,'workflow commands are immutable'); END;
CREATE TRIGGER immutable_workflow_commands_delete BEFORE DELETE ON workflow_commands BEGIN SELECT RAISE(ABORT,'workflow commands are immutable'); END;
CREATE TRIGGER immutable_workflow_transitions_update BEFORE UPDATE ON workflow_transitions BEGIN SELECT RAISE(ABORT,'workflow transitions are append-only'); END;
CREATE TRIGGER immutable_workflow_transitions_delete BEFORE DELETE ON workflow_transitions BEGIN SELECT RAISE(ABORT,'workflow transitions are append-only'); END;
CREATE TRIGGER immutable_research_facts_update BEFORE UPDATE ON research_facts BEGIN SELECT RAISE(ABORT,'research facts are append-only'); END;
CREATE TRIGGER immutable_research_facts_delete BEFORE DELETE ON research_facts BEGIN SELECT RAISE(ABORT,'research facts are append-only'); END;
CREATE TRIGGER immutable_research_fact_refs_update BEFORE UPDATE ON research_fact_refs BEGIN SELECT RAISE(ABORT,'research fact refs are append-only'); END;
CREATE TRIGGER immutable_research_fact_refs_delete BEFORE DELETE ON research_fact_refs BEGIN SELECT RAISE(ABORT,'research fact refs are append-only'); END;
CREATE TRIGGER immutable_execution_artifacts_update BEFORE UPDATE ON execution_artifacts BEGIN SELECT RAISE(ABORT,'execution artifacts are immutable'); END;
CREATE TRIGGER immutable_execution_artifacts_delete BEFORE DELETE ON execution_artifacts BEGIN SELECT RAISE(ABORT,'execution artifacts are immutable'); END;
CREATE TRIGGER immutable_attempt_delete BEFORE DELETE ON execution_attempts BEGIN SELECT RAISE(ABORT,'execution attempts are durable observations'); END;
CREATE TRIGGER immutable_decision_delete BEFORE DELETE ON workflow_human_decisions BEGIN SELECT RAISE(ABORT,'human decisions are durable'); END;
CREATE TRIGGER canonical_outcome_artifact_match BEFORE INSERT ON research_facts WHEN NEW.fact_type='CanonicalOutcomeAccepted' AND NOT EXISTS(SELECT 1 FROM execution_artifacts a WHERE a.artifact_id=NEW.artifact_id AND a.run_id=NEW.run_id AND a.experiment_id=NEW.experiment_id AND a.execution_id=NEW.execution_id AND a.digest=json_extract(NEW.payload_json,'$.artifact_set_digest')) BEGIN SELECT RAISE(ABORT,'canonical outcome artifact identity/digest mismatch'); END;
CREATE TRIGGER execution_attempt_update_rules BEFORE UPDATE ON execution_attempts
WHEN OLD.state<>'OPEN' OR NOT((NEW.state='OPEN' AND OLD.kind='PROCESS' AND OLD.process_group_id IS NULL AND NEW.process_group_id>0 AND NEW.ended_at IS NULL AND NEW.terminal_reason IS NULL AND NEW.observation_json IS NULL) OR (NEW.state='CLOSED' AND (OLD.process_group_id IS NEW.process_group_id OR OLD.process_group_id IS NULL))) OR
 OLD.attempt_id<>NEW.attempt_id OR OLD.run_id<>NEW.run_id OR OLD.workflow_id<>NEW.workflow_id OR OLD.command_id<>NEW.command_id OR OLD.kind<>NEW.kind OR OLD.attempt_ordinal<>NEW.attempt_ordinal OR OLD.host_generation<>NEW.host_generation OR OLD.experiment_id IS NOT NEW.experiment_id OR OLD.execution_id IS NOT NEW.execution_id OR OLD.started_at<>NEW.started_at
BEGIN SELECT RAISE(ABORT,'attempt permits one process bind and one OPEN to CLOSED update'); END;
CREATE TRIGGER human_decision_resolve_once BEFORE UPDATE ON workflow_human_decisions
WHEN OLD.state<>'OPEN' OR NEW.state<>'RESOLVED' OR OLD.decision_id<>NEW.decision_id OR OLD.run_id<>NEW.run_id OR OLD.workflow_id<>NEW.workflow_id OR OLD.family<>NEW.family OR OLD.request_revision<>NEW.request_revision OR OLD.request_json<>NEW.request_json OR OLD.deadline IS NOT NEW.deadline OR OLD.command_id<>NEW.command_id
BEGIN SELECT RAISE(ABORT,'decision permits exactly one OPEN to RESOLVED update'); END;
INSERT INTO schema_migrations(version) VALUES(1);
PRAGMA user_version=1;
COMMIT;
