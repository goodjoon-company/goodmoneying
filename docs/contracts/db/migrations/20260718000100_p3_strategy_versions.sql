-- migrate:up

SET TIME ZONE 'UTC';

CREATE TABLE strategy_definitions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  owner_id TEXT NOT NULL,
  name TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  request_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL,
  reason TEXT NOT NULL,
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (owner_id, name),
  CHECK (btrim(owner_id) <> ''),
  CHECK (btrim(name) <> ''),
  CHECK (btrim(idempotency_key) <> ''),
  CHECK (btrim(request_id) <> ''),
  CHECK (btrim(actor_id) <> ''),
  CHECK (btrim(reason) <> '')
);

CREATE TABLE strategy_versions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  strategy_id BIGINT NOT NULL REFERENCES strategy_definitions(id) ON DELETE RESTRICT,
  version INTEGER NOT NULL CHECK (version > 0),
  schema_version TEXT NOT NULL DEFAULT 'strategy-graph-v1',
  status TEXT NOT NULL CHECK (status IN ('draft','validated','published','retired')),
  graph_hash TEXT NOT NULL CHECK (graph_hash ~ '^[0-9a-f]{64}$'),
  validation_result JSONB NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  request_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL,
  reason TEXT NOT NULL,
  request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  published_at TIMESTAMPTZ,
  retired_at TIMESTAMPTZ,
  UNIQUE (strategy_id, version),
  UNIQUE (strategy_id, graph_hash),
  UNIQUE (id, graph_hash),
  CHECK (schema_version = 'strategy-graph-v1'),
  CHECK (btrim(idempotency_key) <> ''),
  CHECK (btrim(request_id) <> ''),
  CHECK (btrim(actor_id) <> ''),
  CHECK (btrim(reason) <> ''),
  CHECK ((status = 'published') = (published_at IS NOT NULL)),
  CHECK ((status = 'retired') = (retired_at IS NOT NULL))
);

CREATE TABLE strategy_graphs (
  strategy_version_id BIGINT PRIMARY KEY REFERENCES strategy_versions(id) ON DELETE RESTRICT,
  graph_json JSONB NOT NULL,
  graph_hash TEXT NOT NULL CHECK (graph_hash ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (strategy_version_id, graph_hash)
    REFERENCES strategy_versions(id, graph_hash) ON DELETE RESTRICT,
  CHECK (graph_json->>'schema_version' = 'strategy-graph-v1')
);

CREATE TABLE strategy_parameters (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  strategy_version_id BIGINT NOT NULL REFERENCES strategy_versions(id) ON DELETE RESTRICT,
  name TEXT NOT NULL,
  data_type TEXT NOT NULL CHECK (data_type IN ('decimal','integer','boolean','string')),
  default_value JSONB,
  constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (strategy_version_id, name),
  CHECK (btrim(name) <> '')
);

CREATE OR REPLACE FUNCTION reject_strategy_version_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER strategy_versions_append_only_update BEFORE UPDATE ON strategy_versions
  FOR EACH ROW EXECUTE FUNCTION reject_strategy_version_mutation();
CREATE TRIGGER strategy_versions_append_only_delete BEFORE DELETE ON strategy_versions
  FOR EACH ROW EXECUTE FUNCTION reject_strategy_version_mutation();
CREATE TRIGGER strategy_graphs_append_only_update BEFORE UPDATE ON strategy_graphs
  FOR EACH ROW EXECUTE FUNCTION reject_strategy_version_mutation();
CREATE TRIGGER strategy_graphs_append_only_delete BEFORE DELETE ON strategy_graphs
  FOR EACH ROW EXECUTE FUNCTION reject_strategy_version_mutation();
CREATE TRIGGER strategy_parameters_append_only_update BEFORE UPDATE ON strategy_parameters
  FOR EACH ROW EXECUTE FUNCTION reject_strategy_version_mutation();
CREATE TRIGGER strategy_parameters_append_only_delete BEFORE DELETE ON strategy_parameters
  FOR EACH ROW EXECUTE FUNCTION reject_strategy_version_mutation();

GRANT SELECT, INSERT ON strategy_definitions, strategy_versions, strategy_graphs,
  strategy_parameters TO CURRENT_USER;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO CURRENT_USER;

-- migrate:down

DROP TRIGGER IF EXISTS strategy_parameters_append_only_delete ON strategy_parameters;
DROP TRIGGER IF EXISTS strategy_parameters_append_only_update ON strategy_parameters;
DROP TRIGGER IF EXISTS strategy_graphs_append_only_delete ON strategy_graphs;
DROP TRIGGER IF EXISTS strategy_graphs_append_only_update ON strategy_graphs;
DROP TRIGGER IF EXISTS strategy_versions_append_only_delete ON strategy_versions;
DROP TRIGGER IF EXISTS strategy_versions_append_only_update ON strategy_versions;
DROP FUNCTION IF EXISTS reject_strategy_version_mutation();
DROP TABLE IF EXISTS strategy_parameters;
DROP TABLE IF EXISTS strategy_graphs;
DROP TABLE IF EXISTS strategy_versions;
DROP TABLE IF EXISTS strategy_definitions;
