-- migrate:up

SET TIME ZONE 'UTC';

CREATE TABLE portfolios (
  id BIGSERIAL PRIMARY KEY,
  owner_id TEXT NOT NULL,
  name TEXT NOT NULL,
  base_currency TEXT NOT NULL DEFAULT 'KRW',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  created_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  UNIQUE (owner_id, name),
  CHECK (base_currency IN ('KRW','BTC','USDT')),
  CHECK (status IN ('active','archived')),
  CHECK (created_by <> ''),
  CHECK (reason <> '')
);

CREATE TABLE portfolio_policies (
  id BIGSERIAL PRIMARY KEY,
  portfolio_id BIGINT NOT NULL REFERENCES portfolios(id) ON DELETE RESTRICT,
  version INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  max_gross_exposure NUMERIC(38, 18) NOT NULL,
  max_single_position_pct NUMERIC(20, 10) NOT NULL,
  cash_reserve_pct NUMERIC(20, 10) NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  created_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  UNIQUE (portfolio_id, version),
  CHECK (version >= 1),
  CHECK (status IN ('draft','published','retired')),
  CHECK (max_gross_exposure >= 0),
  CHECK (max_single_position_pct >= 0 AND max_single_position_pct <= 1),
  CHECK (cash_reserve_pct >= 0 AND cash_reserve_pct <= 1),
  CHECK (created_by <> ''),
  CHECK (reason <> '')
);

CREATE TABLE capital_allocations (
  id BIGSERIAL PRIMARY KEY,
  portfolio_policy_id BIGINT NOT NULL REFERENCES portfolio_policies(id) ON DELETE RESTRICT,
  scope_type TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  allocation_pct NUMERIC(20, 10) NOT NULL,
  max_notional NUMERIC(38, 18),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (portfolio_policy_id, scope_type, scope_key),
  CHECK (scope_type IN ('global','instrument','strategy','bot')),
  CHECK (scope_key <> ''),
  CHECK (allocation_pct >= 0 AND allocation_pct <= 1),
  CHECK (max_notional IS NULL OR max_notional >= 0)
);

CREATE TABLE bot_definitions (
  id BIGSERIAL PRIMARY KEY,
  owner_id TEXT NOT NULL,
  name TEXT NOT NULL,
  strategy_version_id BIGINT NOT NULL REFERENCES strategy_versions(id) ON DELETE RESTRICT,
  portfolio_id BIGINT NOT NULL REFERENCES portfolios(id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  created_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  UNIQUE (owner_id, name),
  CHECK (created_by <> ''),
  CHECK (reason <> '')
);

CREATE TABLE bot_instances (
  id BIGSERIAL PRIMARY KEY,
  bot_definition_id BIGINT NOT NULL REFERENCES bot_definitions(id) ON DELETE RESTRICT,
  strategy_version_id BIGINT NOT NULL REFERENCES strategy_versions(id) ON DELETE RESTRICT,
  portfolio_policy_id BIGINT NOT NULL REFERENCES portfolio_policies(id) ON DELETE RESTRICT,
  backtest_run_id BIGINT REFERENCES backtest_runs(id) ON DELETE RESTRICT,
  stage TEXT NOT NULL DEFAULT 'draft',
  previous_stage TEXT,
  execution_mode TEXT NOT NULL,
  started_at TIMESTAMPTZ,
  stopped_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  created_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  CHECK (stage IN ('draft','backtest','paper','shadow','paused','stopped','faulted')),
  CHECK (previous_stage IS NULL OR previous_stage IN ('draft','backtest','paper','shadow')),
  CHECK (execution_mode IN ('paper','shadow')),
  CHECK (stopped_at IS NULL OR started_at IS NULL OR stopped_at >= started_at),
  CHECK (created_by <> ''),
  CHECK (reason <> '')
);

CREATE TABLE bot_state_transitions (
  id BIGSERIAL PRIMARY KEY,
  bot_instance_id BIGINT NOT NULL REFERENCES bot_instances(id) ON DELETE RESTRICT,
  from_stage TEXT,
  to_stage TEXT NOT NULL,
  request_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (bot_instance_id, request_id),
  CHECK (from_stage IS NULL OR from_stage IN ('draft','backtest','paper','shadow','paused','stopped','faulted')),
  CHECK (to_stage IN ('draft','backtest','paper','shadow','paused','stopped','faulted')),
  CHECK (actor_id <> ''),
  CHECK (reason <> ''),
  CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE TABLE order_intents (
  id BIGSERIAL PRIMARY KEY,
  bot_instance_id BIGINT NOT NULL REFERENCES bot_instances(id) ON DELETE RESTRICT,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  idempotency_key TEXT NOT NULL,
  side TEXT NOT NULL,
  order_type TEXT NOT NULL,
  requested_quantity NUMERIC(38, 18),
  requested_notional NUMERIC(38, 18),
  limit_price NUMERIC(38, 18),
  status TEXT NOT NULL DEFAULT 'created',
  decision_input_hash TEXT NOT NULL,
  risk_policy_version INTEGER,
  risk_decision_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  created_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  UNIQUE (bot_instance_id, idempotency_key),
  CHECK (side IN ('buy','sell')),
  CHECK (order_type IN ('market','limit')),
  CHECK (requested_quantity IS NOT NULL OR requested_notional IS NOT NULL),
  CHECK (requested_quantity IS NULL OR requested_quantity > 0),
  CHECK (requested_notional IS NULL OR requested_notional > 0),
  CHECK (limit_price IS NULL OR limit_price > 0),
  CHECK (status IN ('created','risk_rejected','approved','paper_filled','shadow_observed','outcome_unknown','reconciled','cancelled','completed')),
  CHECK (decision_input_hash ~ '^[0-9a-f]{64}$'),
  CHECK (risk_policy_version IS NULL OR risk_policy_version >= 1),
  CHECK (created_by <> ''),
  CHECK (reason <> '')
);

CREATE TABLE exchange_orders (
  id BIGSERIAL PRIMARY KEY,
  order_intent_id BIGINT NOT NULL REFERENCES order_intents(id) ON DELETE RESTRICT,
  execution_mode TEXT NOT NULL,
  simulated_order_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending_submit',
  submitted_at TIMESTAMPTZ,
  reconciled_at TIMESTAMPTZ,
  raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (order_intent_id, simulated_order_key),
  CHECK (execution_mode IN ('paper','shadow')),
  CHECK (status IN ('pending_submit','wait','watch','trade','partially_filled','done','cancel','prevented','rejected','outcome_unknown','reconciled')),
  CHECK (simulated_order_key <> ''),
  CHECK (jsonb_typeof(raw_payload) = 'object')
);

CREATE TABLE order_fills (
  id BIGSERIAL PRIMARY KEY,
  exchange_order_id BIGINT NOT NULL REFERENCES exchange_orders(id) ON DELETE RESTRICT,
  fill_sequence INTEGER NOT NULL,
  fill_source TEXT NOT NULL,
  side TEXT NOT NULL,
  filled_quantity NUMERIC(38, 18) NOT NULL,
  fill_price NUMERIC(38, 18) NOT NULL,
  fee_paid NUMERIC(38, 18) NOT NULL DEFAULT 0,
  occurred_at TIMESTAMPTZ NOT NULL,
  knowledge_at TIMESTAMPTZ NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (exchange_order_id, fill_sequence),
  CHECK (fill_sequence >= 1),
  CHECK (fill_source IN ('paper_simulator','shadow_observation','reconciliation')),
  CHECK (side IN ('buy','sell')),
  CHECK (filled_quantity > 0),
  CHECK (fill_price > 0),
  CHECK (fee_paid >= 0),
  CHECK (knowledge_at >= occurred_at),
  CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE TABLE position_projections (
  id BIGSERIAL PRIMARY KEY,
  portfolio_id BIGINT NOT NULL REFERENCES portfolios(id) ON DELETE RESTRICT,
  instrument_id BIGINT NOT NULL REFERENCES instruments(id),
  quantity NUMERIC(38, 18) NOT NULL DEFAULT 0,
  average_entry_price NUMERIC(38, 18),
  realized_pnl NUMERIC(38, 18) NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  source_fill_id BIGINT REFERENCES order_fills(id) ON DELETE RESTRICT,
  UNIQUE (portfolio_id, instrument_id),
  CHECK (average_entry_price IS NULL OR average_entry_price >= 0)
);

CREATE TABLE risk_limits (
  id BIGSERIAL PRIMARY KEY,
  scope_type TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  limit_type TEXT NOT NULL,
  version INTEGER NOT NULL,
  limit_value NUMERIC(38, 18) NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  UNIQUE (scope_type, scope_key, limit_type, version),
  CHECK (scope_type IN ('global','portfolio','bot','instrument')),
  CHECK (scope_key <> ''),
  CHECK (limit_type IN ('max_order_notional','max_daily_loss','max_position_notional','max_drawdown','max_open_orders')),
  CHECK (version >= 1),
  CHECK (limit_value >= 0),
  CHECK (status IN ('active','retired')),
  CHECK (actor_id <> ''),
  CHECK (reason <> '')
);

CREATE TABLE risk_events (
  id BIGSERIAL PRIMARY KEY,
  order_intent_id BIGINT REFERENCES order_intents(id) ON DELETE RESTRICT,
  bot_instance_id BIGINT REFERENCES bot_instances(id) ON DELETE RESTRICT,
  scope_type TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  risk_policy_version INTEGER,
  message TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (scope_type, scope_key, fingerprint),
  CHECK (scope_type IN ('global','portfolio','bot','instrument')),
  CHECK (scope_key <> ''),
  CHECK (event_type IN ('policy_approved','limit_rejected','kill_switch_rejected','reconciliation_mismatch','outcome_unknown')),
  CHECK (severity IN ('info','warning','critical')),
  CHECK (risk_policy_version IS NULL OR risk_policy_version >= 1),
  CHECK (message <> ''),
  CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE TABLE kill_switches (
  id BIGSERIAL PRIMARY KEY,
  scope_type TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  state TEXT NOT NULL,
  sequence BIGINT NOT NULL,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  open_order_policy TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (scope_type, scope_key, sequence),
  CHECK (scope_type IN ('global','portfolio','bot','account')),
  CHECK (scope_key <> ''),
  CHECK (state IN ('armed','released')),
  CHECK (sequence >= 1),
  CHECK (actor_id <> ''),
  CHECK (reason <> ''),
  CHECK (open_order_policy IN ('leave_open','cancel_open')),
  CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE OR REPLACE FUNCTION reject_p5_append_only_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER bot_state_transitions_append_only_update
  BEFORE UPDATE ON bot_state_transitions
  FOR EACH ROW EXECUTE FUNCTION reject_p5_append_only_mutation();
CREATE TRIGGER bot_state_transitions_append_only_delete
  BEFORE DELETE ON bot_state_transitions
  FOR EACH ROW EXECUTE FUNCTION reject_p5_append_only_mutation();
CREATE TRIGGER order_fills_append_only_update
  BEFORE UPDATE ON order_fills
  FOR EACH ROW EXECUTE FUNCTION reject_p5_append_only_mutation();
CREATE TRIGGER order_fills_append_only_delete
  BEFORE DELETE ON order_fills
  FOR EACH ROW EXECUTE FUNCTION reject_p5_append_only_mutation();
CREATE TRIGGER risk_events_append_only_update
  BEFORE UPDATE ON risk_events
  FOR EACH ROW EXECUTE FUNCTION reject_p5_append_only_mutation();
CREATE TRIGGER risk_events_append_only_delete
  BEFORE DELETE ON risk_events
  FOR EACH ROW EXECUTE FUNCTION reject_p5_append_only_mutation();
CREATE TRIGGER kill_switches_append_only_update
  BEFORE UPDATE ON kill_switches
  FOR EACH ROW EXECUTE FUNCTION reject_p5_append_only_mutation();
CREATE TRIGGER kill_switches_append_only_delete
  BEFORE DELETE ON kill_switches
  FOR EACH ROW EXECUTE FUNCTION reject_p5_append_only_mutation();

CREATE INDEX bot_instances_stage_idx ON bot_instances(stage, execution_mode);
CREATE INDEX order_intents_status_idx ON order_intents(status, created_at);
CREATE INDEX exchange_orders_status_idx ON exchange_orders(status, submitted_at);
CREATE INDEX risk_limits_active_idx ON risk_limits(scope_type, scope_key, limit_type)
  WHERE status = 'active';
CREATE INDEX kill_switches_scope_state_idx ON kill_switches(scope_type, scope_key, state, sequence DESC);

-- migrate:down

SET TIME ZONE 'UTC';

DROP TABLE IF EXISTS kill_switches;
DROP TABLE IF EXISTS risk_events;
DROP TABLE IF EXISTS risk_limits;
DROP TABLE IF EXISTS position_projections;
DROP TABLE IF EXISTS order_fills;
DROP TABLE IF EXISTS exchange_orders;
DROP TABLE IF EXISTS order_intents;
DROP TABLE IF EXISTS bot_state_transitions;
DROP TABLE IF EXISTS bot_instances;
DROP TABLE IF EXISTS bot_definitions;
DROP TABLE IF EXISTS capital_allocations;
DROP TABLE IF EXISTS portfolio_policies;
DROP TABLE IF EXISTS portfolios;
DROP FUNCTION IF EXISTS reject_p5_append_only_mutation();
