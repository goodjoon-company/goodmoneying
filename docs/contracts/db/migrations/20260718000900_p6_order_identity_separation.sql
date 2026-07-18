-- migrate:up

SET TIME ZONE 'UTC';

CREATE TABLE exchange_accounts (
  id BIGSERIAL PRIMARY KEY,
  exchange TEXT NOT NULL,
  account_stable_id TEXT NOT NULL,
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'live_disabled',
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  created_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  UNIQUE (exchange, account_stable_id),
  CHECK (exchange = 'upbit'),
  CHECK (account_stable_id ~ '^[A-Za-z0-9:_-]{3,128}$'),
  CHECK (label <> ''),
  CHECK (status IN ('live_disabled','live_ready','live_enabled','revoked')),
  CHECK (created_by <> ''),
  CHECK (reason <> '')
);

CREATE TABLE upbit_order_identifier_reservations (
  id BIGSERIAL PRIMARY KEY,
  exchange_account_id BIGINT NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
  identifier TEXT NOT NULL,
  source_table TEXT NOT NULL,
  source_column TEXT NOT NULL,
  source_id BIGINT NOT NULL,
  reserved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (exchange_account_id, identifier),
  UNIQUE (source_table, source_column, source_id),
  CHECK (identifier <> ''),
  CHECK (
    source_table IN (
      'live_order_identifiers',
      'upbit_order_test_runs'
    )
  ),
  CHECK (
    source_column IN (
      'identifier',
      'response_uuid',
      'response_identifier'
    )
  )
);

CREATE TABLE live_order_identifiers (
  id BIGSERIAL PRIMARY KEY,
  exchange_account_id BIGINT NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
  order_intent_id BIGINT NOT NULL REFERENCES order_intents(id) ON DELETE RESTRICT,
  idempotency_key TEXT NOT NULL,
  identifier TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'reserved',
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  created_by TEXT NOT NULL,
  reason TEXT NOT NULL,
  UNIQUE (exchange_account_id, identifier),
  UNIQUE (order_intent_id),
  CHECK (idempotency_key <> ''),
  CHECK (identifier ~ '^gm1_[a-z2-7]{52}$'),
  CHECK (status IN ('reserved','submitted','outcome_unknown','retired')),
  CHECK (created_by <> ''),
  CHECK (reason <> '')
);

CREATE TABLE upbit_order_test_runs (
  id BIGSERIAL PRIMARY KEY,
  exchange_account_id BIGINT NOT NULL REFERENCES exchange_accounts(id) ON DELETE RESTRICT,
  request_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL,
  request_parameters JSONB NOT NULL,
  response_status_code INTEGER NOT NULL,
  response_uuid TEXT,
  response_identifier TEXT,
  response_body JSONB NOT NULL,
  lookup_allowed BOOLEAN NOT NULL DEFAULT FALSE,
  cancel_allowed BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (exchange_account_id, request_id),
  CHECK (request_id <> ''),
  CHECK (actor_id <> ''),
  CHECK (reason <> ''),
  CHECK (response_status_code >= 100 AND response_status_code <= 599),
  CHECK (jsonb_typeof(request_parameters) = 'object'),
  CHECK (jsonb_typeof(response_body) = 'object'),
  CHECK (lookup_allowed = FALSE),
  CHECK (cancel_allowed = FALSE),
  CHECK (created_at >= requested_at)
);

CREATE FUNCTION p6_base32lower_no_padding(value BYTEA)
RETURNS TEXT AS $$
DECLARE
  alphabet TEXT := 'abcdefghijklmnopqrstuvwxyz234567';
  output TEXT := '';
  buffer BIGINT := 0;
  bit_count INTEGER := 0;
  byte_value INTEGER;
  index_value INTEGER;
  byte_index INTEGER;
BEGIN
  FOR byte_index IN 0..length(value) - 1 LOOP
    byte_value := get_byte(value, byte_index);
    buffer := (buffer << 8) | byte_value;
    bit_count := bit_count + 8;
    WHILE bit_count >= 5 LOOP
      index_value := (buffer >> (bit_count - 5)) & 31;
      output := output || substr(alphabet, index_value + 1, 1);
      bit_count := bit_count - 5;
      buffer := buffer & ((1::BIGINT << bit_count) - 1);
    END LOOP;
  END LOOP;
  IF bit_count > 0 THEN
    index_value := (buffer << (5 - bit_count)) & 31;
    output := output || substr(alphabet, index_value + 1, 1);
  END IF;
  RETURN output;
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT;

CREATE FUNCTION p6_upbit_live_order_identifier(
  account_stable_id TEXT,
  idempotency_key TEXT
)
RETURNS TEXT AS $$
  SELECT 'gm1_' || p6_base32lower_no_padding(
    sha256(convert_to(account_stable_id || ':' || idempotency_key, 'UTF8'))
  );
$$ LANGUAGE sql IMMUTABLE STRICT;

CREATE FUNCTION reserve_p6_upbit_order_identifier(
  exchange_account_id_value BIGINT,
  identifier_value TEXT,
  source_table_value TEXT,
  source_column_value TEXT,
  source_id_value BIGINT
)
RETURNS VOID AS $$
BEGIN
  IF identifier_value IS NULL OR identifier_value = '' THEN
    RETURN;
  END IF;

  INSERT INTO upbit_order_identifier_reservations (
    exchange_account_id,
    identifier,
    source_table,
    source_column,
    source_id
  ) VALUES (
    exchange_account_id_value,
    identifier_value,
    source_table_value,
    source_column_value,
    source_id_value
  )
  ON CONFLICT (exchange_account_id, identifier) DO UPDATE
    SET reserved_at = upbit_order_identifier_reservations.reserved_at
    WHERE upbit_order_identifier_reservations.source_table = source_table_value
      AND upbit_order_identifier_reservations.source_column = source_column_value
      AND upbit_order_identifier_reservations.source_id = source_id_value;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Upbit order identifier is already reserved for another source';
  END IF;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION validate_p6_live_order_identifier()
RETURNS TRIGGER AS $$
DECLARE
  actual_idempotency_key TEXT;
  account_stable_id_value TEXT;
  expected_identifier TEXT;
BEGIN
  SELECT intent.idempotency_key, account.account_stable_id
    INTO actual_idempotency_key, account_stable_id_value
  FROM order_intents intent
  JOIN exchange_accounts account ON account.id = NEW.exchange_account_id
  WHERE intent.id = NEW.order_intent_id;

  IF actual_idempotency_key IS NULL THEN
    RAISE EXCEPTION 'live order identifier references missing account or order intent';
  END IF;
  IF NEW.idempotency_key <> actual_idempotency_key THEN
    RAISE EXCEPTION 'live order identifier idempotency_key must match order_intents.idempotency_key';
  END IF;

  expected_identifier := p6_upbit_live_order_identifier(
    account_stable_id_value,
    actual_idempotency_key
  );
  IF NEW.identifier <> expected_identifier THEN
    RAISE EXCEPTION 'live order identifier must be derived from account_stable_id and order intent idempotency_key';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM upbit_order_test_runs test_run
    WHERE test_run.exchange_account_id = NEW.exchange_account_id
      AND NEW.identifier IN (
        test_run.response_uuid,
        test_run.response_identifier
      )
  ) THEN
    RAISE EXCEPTION 'order-test response identifier cannot be reserved as a live order identifier';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION reserve_p6_live_order_identifier()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM reserve_p6_upbit_order_identifier(
    NEW.exchange_account_id,
    NEW.identifier,
    'live_order_identifiers',
    'identifier',
    NEW.id
  );

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION validate_p6_order_test_identifier_not_live()
RETURNS TRIGGER AS $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM live_order_identifiers live_identifier
    WHERE live_identifier.exchange_account_id = NEW.exchange_account_id
      AND live_identifier.identifier IN (
        NEW.response_uuid,
        NEW.response_identifier
      )
  ) THEN
    RAISE EXCEPTION 'live order identifier cannot be recorded as an order-test response identifier';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION reserve_p6_order_test_identifier()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM reserve_p6_upbit_order_identifier(
    NEW.exchange_account_id,
    NEW.response_uuid,
    'upbit_order_test_runs',
    'response_uuid',
    NEW.id
  );

  IF NEW.response_identifier IS DISTINCT FROM NEW.response_uuid THEN
    PERFORM reserve_p6_upbit_order_identifier(
      NEW.exchange_account_id,
      NEW.response_identifier,
      'upbit_order_test_runs',
      'response_identifier',
      NEW.id
    );
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION reject_p6_order_test_run_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'Upbit order-test run evidence is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER live_order_identifiers_validate_identity
  BEFORE INSERT OR UPDATE ON live_order_identifiers
  FOR EACH ROW EXECUTE FUNCTION validate_p6_live_order_identifier();

CREATE TRIGGER upbit_order_test_runs_reject_live_identifier
  BEFORE INSERT OR UPDATE ON upbit_order_test_runs
  FOR EACH ROW EXECUTE FUNCTION validate_p6_order_test_identifier_not_live();

CREATE TRIGGER live_order_identifiers_reserve_identifier
  AFTER INSERT OR UPDATE ON live_order_identifiers
  FOR EACH ROW EXECUTE FUNCTION reserve_p6_live_order_identifier();

CREATE TRIGGER upbit_order_test_runs_reserve_identifiers
  AFTER INSERT ON upbit_order_test_runs
  FOR EACH ROW EXECUTE FUNCTION reserve_p6_order_test_identifier();

CREATE TRIGGER upbit_order_test_runs_reject_mutation
  BEFORE UPDATE OR DELETE ON upbit_order_test_runs
  FOR EACH ROW EXECUTE FUNCTION reject_p6_order_test_run_mutation();

CREATE INDEX live_order_identifiers_status_idx
  ON live_order_identifiers (status, created_at, id);

CREATE INDEX upbit_order_test_runs_requested_idx
  ON upbit_order_test_runs (exchange_account_id, requested_at DESC, id DESC);

-- migrate:down

SET TIME ZONE 'UTC';

DROP INDEX IF EXISTS upbit_order_test_runs_requested_idx;
DROP INDEX IF EXISTS live_order_identifiers_status_idx;
DROP TRIGGER IF EXISTS upbit_order_test_runs_reserve_identifiers ON upbit_order_test_runs;
DROP TRIGGER IF EXISTS live_order_identifiers_reserve_identifier ON live_order_identifiers;
DROP TRIGGER IF EXISTS upbit_order_test_runs_reject_mutation ON upbit_order_test_runs;
DROP TRIGGER IF EXISTS upbit_order_test_runs_reject_live_identifier ON upbit_order_test_runs;
DROP TRIGGER IF EXISTS live_order_identifiers_validate_identity ON live_order_identifiers;
DROP FUNCTION IF EXISTS reject_p6_order_test_run_mutation();
DROP FUNCTION IF EXISTS reserve_p6_order_test_identifier();
DROP FUNCTION IF EXISTS validate_p6_order_test_identifier_not_live();
DROP FUNCTION IF EXISTS reserve_p6_live_order_identifier();
DROP FUNCTION IF EXISTS validate_p6_live_order_identifier();
DROP FUNCTION IF EXISTS reserve_p6_upbit_order_identifier(BIGINT, TEXT, TEXT, TEXT, BIGINT);
DROP FUNCTION IF EXISTS p6_upbit_live_order_identifier(TEXT, TEXT);
DROP FUNCTION IF EXISTS p6_base32lower_no_padding(BYTEA);
DROP TABLE IF EXISTS upbit_order_test_runs;
DROP TABLE IF EXISTS live_order_identifiers;
DROP TABLE IF EXISTS upbit_order_identifier_reservations;
DROP TABLE IF EXISTS exchange_accounts;
