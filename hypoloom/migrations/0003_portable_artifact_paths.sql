BEGIN IMMEDIATE;
CREATE TEMP TABLE migration_0003_artifact_path_guard(ok INTEGER NOT NULL CHECK(ok=1));
INSERT INTO migration_0003_artifact_path_guard
SELECT CASE WHEN path=stable_path OR substr(path,-length(stable_path)-1)='/'||stable_path THEN 1 ELSE 0 END
FROM (SELECT path,run_id||'/'||experiment_id||'/'||execution_id||'/'||attempt_id||'/'||artifact_kind AS stable_path FROM execution_artifacts);
DROP TRIGGER immutable_execution_artifacts_update;
UPDATE execution_artifacts SET path=run_id||'/'||experiment_id||'/'||execution_id||'/'||attempt_id||'/'||artifact_kind;
CREATE TRIGGER immutable_execution_artifacts_update BEFORE UPDATE ON execution_artifacts BEGIN SELECT RAISE(ABORT,'execution artifacts are immutable'); END;
DROP TABLE migration_0003_artifact_path_guard;
INSERT INTO schema_migrations(version) VALUES(3); PRAGMA user_version=3; COMMIT;
