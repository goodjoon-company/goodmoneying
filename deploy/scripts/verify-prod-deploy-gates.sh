#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "운영 배포 사전점검 실패: $*" >&2
  exit 1
}

lower_hex() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

for name in GH_TOKEN GITHUB_REPOSITORY GITHUB_REF GITHUB_SHA APPROVED_SHA; do
  if [[ -z "${!name:-}" ]]; then
    fail "${name} 환경 변수가 비어 있습니다."
  fi
done

github_sha_lower="$(lower_hex "${GITHUB_SHA}")"
deploy_enable_sha_lower="$(lower_hex "${DEPLOY_ENABLE_SHA:-}")"

if [[ -z "${DEPLOY_ENABLE_SHA:-}" || "${deploy_enable_sha_lower}" != "${github_sha_lower}" ]]; then
  fail "P8 배포 잠금이 해제되지 않았습니다. GOODMONEYING_PROD_DEPLOY_ENABLE_SHA가 실행 SHA와 일치해야 합니다."
fi

if [[ ! "${APPROVED_SHA}" =~ ^[0-9a-fA-F]{40}$ ]]; then
  fail "approved_sha는 40자리 commit SHA여야 합니다."
fi
if [[ "$(lower_hex "${APPROVED_SHA}")" != "${github_sha_lower}" ]]; then
  fail "approved_sha와 workflow commit SHA가 일치하지 않습니다."
fi
if [[ "${GITHUB_REF}" != "refs/heads/release" ]]; then
  fail "release 브랜치에서 수동 실행해야 합니다: ${GITHUB_REF}"
fi

require_api_truth() {
  local endpoint="$1"
  local expression="$2"
  local description="$3"
  local result
  result="$(gh api "${endpoint}" --jq "${expression}")" || fail "${description} GitHub API 증명에 실패했습니다."
  [[ "${result}" == "true" || "${result}" == "ok" || "${result}" == "1" ]] || fail "${description} 조건이 충족되지 않았습니다."
}

protection_expression='(.required_status_checks.strict == true) and (.required_status_checks.contexts | index("verify") != null) and (.enforce_admins.enabled == true) and (.required_pull_request_reviews.required_approving_review_count >= 1) and (.allow_force_pushes.enabled == false) and (.allow_deletions.enabled == false)'
require_api_truth "repos/${GITHUB_REPOSITORY}/branches/main/protection" "${protection_expression}" "main 보호"
require_api_truth "repos/${GITHUB_REPOSITORY}/branches/release/protection" "${protection_expression}" "release 보호"
require_api_truth "repos/${GITHUB_REPOSITORY}/environments/prod" '([.protection_rules[]? | select(.type == "required_reviewers")] | length > 0) and (.deployment_branch_policy.protected_branches == true)' "prod 승인과 배포 브랜치 제한"

main_sha="$(gh api "repos/${GITHUB_REPOSITORY}/commits/main" --jq .sha)" || fail "main SHA를 조회하지 못했습니다."
release_sha="$(gh api "repos/${GITHUB_REPOSITORY}/commits/release" --jq .sha)" || fail "release SHA를 조회하지 못했습니다."
[[ "${main_sha}" == "${GITHUB_SHA}" ]] || fail "main SHA와 배포 SHA가 일치하지 않습니다."
[[ "${release_sha}" == "${GITHUB_SHA}" ]] || fail "release SHA와 배포 SHA가 일치하지 않습니다."

require_api_truth "repos/${GITHUB_REPOSITORY}/commits/${GITHUB_SHA}/check-runs" '[.check_runs[] | select(.name == "verify" and .conclusion == "success")] | length' "배포 SHA의 CI verify 성공"

echo "운영 배포 사전점검 통과: ${GITHUB_SHA}"
