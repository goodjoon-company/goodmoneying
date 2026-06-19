# prod-home 배포 프로필

`prod-home`은 운영계(prod)를 홈 인프라(home)에 배포하는 프로필이다.

## 서버 역할

| 서버 | 역할 | 서비스 |
|---|---|---|
| Mac Mini M4 | infra, 배포 제어 | postgres, GitHub Actions runner |
| APP SERVER 01 | application | api, worker |
| bmax-ubuntu | web | web |

## 비밀값

비밀값은 repo에 커밋하지 않는다. 각 서버의 `/opt/goodmoneying/env/` 아래에 둔다.

- `/opt/goodmoneying/env/infra.env`
- `/opt/goodmoneying/env/app.env`
- `/opt/goodmoneying/env/web.env`
- `/opt/goodmoneying/env/ghcr.env`

## 수동 dry-run

```bash
GOODMONEYING_DEPLOY_DRY_RUN=1 deploy/scripts/deploy-profile.sh prod-home release-abc1234
```
