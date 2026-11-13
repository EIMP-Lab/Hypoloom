BEGIN IMMEDIATE;
-- Access paths only: the ordinary commands, attempts and transitions remain
-- authoritative. Existing nonempty runs retain their frozen schema/bundle.
CREATE INDEX execution_attempt_command_read ON execution_attempts(command_id,attempt_ordinal);
CREATE INDEX execution_attempt_execution_read ON execution_attempts(execution_id,attempt_ordinal);
CREATE INDEX execution_attempt_run_state_read ON execution_attempts(run_id,state,kind);
CREATE INDEX execution_agent_resume_read ON execution_attempts(
 run_id,json_extract(observation_json,'$.request.command'),command_id,
 state,terminal_reason,json_extract(observation_json,'$.request.schema_version'),
 json_extract(observation_json,'$.response.typed_candidate.waiting_for_experiment_id'),attempt_ordinal
) WHERE kind='AGENT';
CREATE INDEX workflow_final_failure_read ON workflow_transitions(
 run_id,json_extract(payload_json,'$.failed_task_command_id')
) WHERE json_extract(payload_json,'$.agent_final_failure')=1;
INSERT INTO schema_migrations(version) VALUES(4);
PRAGMA user_version=4;
COMMIT;
