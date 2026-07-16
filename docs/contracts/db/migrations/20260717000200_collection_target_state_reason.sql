-- migrate:up

-- 수집 대상 상태의 명시적 원인을 기록해 운영자 명령과 카탈로그 변화를 구분한다.

SET TIME ZONE 'UTC';

ALTER TABLE collection_target_specs
  ADD COLUMN state_reason TEXT;

UPDATE collection_target_specs AS spec
SET state_reason = CASE
  WHEN status = 'active' THEN NULL
  WHEN status = 'excluded' THEN 'operator_excluded'
  WHEN exclusion_reason = 'policy:data-type-disabled'
    THEN 'policy_data_type_disabled'
  WHEN NOT auto_managed AND (
    SELECT log.after_data ->> 'state'
    FROM audit_logs AS log
    JOIN markets AS market ON market.market_code = log.target_id
    WHERE log.action = 'market_target_state_changed'
      AND log.target_type = 'market'
      AND market.id = spec.market_id
    ORDER BY log.created_at DESC, log.id DESC
    LIMIT 1
  ) = 'paused' THEN 'operator_paused'
  ELSE 'market_inactive'
END;

ALTER TABLE collection_target_specs
  ADD CONSTRAINT collection_target_specs_state_reason_ck CHECK (
    (status = 'active' AND state_reason IS NULL)
    OR (
      status = 'paused'
      AND state_reason IS NOT NULL
      AND state_reason IN (
        'catalog_missing', 'market_inactive', 'operator_paused',
        'policy_data_type_disabled'
      )
    )
    OR (
      status = 'excluded'
      AND state_reason IS NOT NULL
      AND state_reason = 'operator_excluded'
    )
  );

COMMENT ON COLUMN collection_target_specs.state_reason IS
  '상태 원인: catalog_missing, market_inactive, operator_paused, operator_excluded, policy_data_type_disabled';

-- migrate:down

-- 상태 원인은 운영자의 명시적 일시정지 보존에 필요하므로 자동 수축하지 않는다.
