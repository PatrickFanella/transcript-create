WITH role_row AS (
    SELECT oid
    FROM pg_roles
    WHERE rolname = 'hasanara_diarization'
      AND rolcanlogin
      AND NOT rolsuper
      AND NOT rolcreaterole
      AND NOT rolcreatedb
      AND NOT rolreplication
      AND NOT rolbypassrls
), required_grants(table_name, column_name, privilege_type) AS (
    VALUES
        ('videos', 'id', 'SELECT'), ('videos', 'state', 'SELECT'), ('videos', 'wav_path', 'SELECT'),
        ('videos', 'diarization_state', 'SELECT'), ('videos', 'diarization_error', 'SELECT'), ('videos', 'duration_seconds', 'SELECT'),
        ('videos', 'updated_at', 'SELECT'), ('videos', 'created_at', 'SELECT'),
        ('videos', 'diarization_state', 'UPDATE'), ('videos', 'diarization_error', 'UPDATE'),
        ('videos', 'updated_at', 'UPDATE'), ('segments', 'id', 'SELECT'),
        ('segments', 'video_id', 'SELECT'), ('segments', 'start_ms', 'SELECT'),
        ('segments', 'end_ms', 'SELECT'), ('segments', 'text', 'SELECT'),
        ('segments', 'speaker_label', 'SELECT'), ('segments', 'confidence', 'SELECT'),
        ('segments', 'avg_logprob', 'SELECT'), ('segments', 'temperature', 'SELECT'),
        ('segments', 'token_count', 'SELECT'), ('segments', 'speaker_label', 'UPDATE')
), effective_target_grants AS (
    SELECT c.table_name, c.column_name, p.privilege_type
    FROM information_schema.columns c
    CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('REFERENCES')) p(privilege_type)
    WHERE c.table_schema = 'public'
      AND c.table_name IN ('videos', 'segments')
      AND has_column_privilege('hasanara_diarization', format('%I.%I', c.table_schema, c.table_name), c.column_name, p.privilege_type)
), non_system_routines AS (
    SELECT p.oid, p.proowner
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname !~ '^pg_'
      AND n.nspname <> 'information_schema'
), non_system_types AS (
    SELECT t.oid, t.typowner
    FROM pg_type t
    JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE n.nspname !~ '^pg_'
      AND n.nspname <> 'information_schema'
), non_system_relations AS (
    SELECT c.oid, c.relkind, c.relname, n.nspname, c.relowner
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname !~ '^pg_'
      AND n.nspname <> 'information_schema'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
)
SELECT 1 / CASE WHEN
    EXISTS (SELECT 1 FROM role_row)
    AND has_schema_privilege('hasanara_diarization', 'public', 'USAGE')
    AND has_database_privilege('hasanara_diarization', current_database(), 'CONNECT')
    AND NOT has_database_privilege('hasanara_diarization', current_database(), 'CREATE')
    AND EXISTS (
        SELECT 1
        FROM pg_namespace n
        CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl, acldefault('n', n.nspowner))) a
        WHERE n.nspname = 'public' AND a.grantee = 0 AND a.privilege_type = 'USAGE'
    )
    AND NOT EXISTS (SELECT 1 FROM pg_auth_members m JOIN role_row r ON m.member = r.oid OR m.roleid = r.oid)
    AND NOT EXISTS (SELECT 1 FROM pg_database d JOIN role_row r ON d.datdba = r.oid)
    AND NOT EXISTS (SELECT 1 FROM pg_namespace n JOIN role_row r ON n.nspowner = r.oid WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema')
    AND NOT EXISTS (SELECT 1 FROM non_system_relations c JOIN role_row r ON c.relowner = r.oid)
    AND NOT EXISTS (SELECT 1 FROM non_system_routines p JOIN role_row r ON p.proowner = r.oid)
    AND NOT EXISTS (SELECT 1 FROM non_system_types t JOIN role_row r ON t.typowner = r.oid)
    AND NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND has_schema_privilege('hasanara_diarization', n.oid, 'CREATE'))
    AND NOT EXISTS (
        (SELECT * FROM required_grants EXCEPT SELECT * FROM effective_target_grants)
        UNION ALL
        (SELECT * FROM effective_target_grants EXCEPT SELECT * FROM required_grants)
    )
    AND NOT EXISTS (
        SELECT 1
        FROM non_system_relations c
        WHERE c.nspname = 'public'
          AND c.relname IN ('videos', 'segments')
          AND c.relkind <> 'S'
          AND (
              has_table_privilege('hasanara_diarization', c.oid, 'DELETE')
              OR has_table_privilege('hasanara_diarization', c.oid, 'TRUNCATE')
              OR has_table_privilege('hasanara_diarization', c.oid, 'TRIGGER')
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM non_system_relations c
        WHERE NOT (c.nspname = 'public' AND c.relname IN ('videos', 'segments'))
          AND (
              (c.relkind = 'S' AND (
                  has_sequence_privilege('hasanara_diarization', c.oid, 'USAGE')
                  OR has_sequence_privilege('hasanara_diarization', c.oid, 'SELECT')
                  OR has_sequence_privilege('hasanara_diarization', c.oid, 'UPDATE')
              ))
              OR (c.relkind <> 'S' AND (
                  has_table_privilege('hasanara_diarization', c.oid, 'SELECT')
                  OR has_table_privilege('hasanara_diarization', c.oid, 'INSERT')
                  OR has_table_privilege('hasanara_diarization', c.oid, 'UPDATE')
                  OR has_table_privilege('hasanara_diarization', c.oid, 'DELETE')
                  OR has_table_privilege('hasanara_diarization', c.oid, 'TRUNCATE')
                  OR has_table_privilege('hasanara_diarization', c.oid, 'REFERENCES')
                  OR has_table_privilege('hasanara_diarization', c.oid, 'TRIGGER')
              ))
          )
    )
THEN 1 ELSE 0 END;
