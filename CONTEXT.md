# goodmoneying

goodmoneying은 개인 투자자가 시장 데이터, 분석 신호, 전략, 봇을 단계적으로 구축해 투자 의사결정 자동화를 준비하는 제품 맥락이다.

## Language

**업비트 수집 파이프라인(Upbit Collection Pipeline)**:
업비트(Upbit) KRW 마켓 데이터를 수집, 저장, 품질 확인, 운영 상태 노출까지 책임지는 M1 핵심 경계다.
_Avoid_: 시장 데이터 플랫폼, 업비트 모듈

**수집 워커(Collection Worker)**:
외부 시장 데이터 공급원에서 데이터를 가져와 저장하고 수집 품질을 기록하는 상시 실행 프로세스다.
_Avoid_: 크롤러, 배치, 스케줄러

**운영 서버(Operations Server)**:
수집 상태와 저장된 시장 데이터를 조회할 수 있도록 API와 운영 화면을 제공하는 프로세스다.
_Avoid_: 대시보드 서버, 웹 서버

**캔들(Candle)**:
정해진 시간 구간의 시가, 고가, 저가, 종가, 거래량, 거래대금을 나타내는 완성 또는 기준 시점이 있는 시장 데이터다.
_Avoid_: OHLCV, 봉 데이터

**원천 캔들(Source Candle)**:
외부 데이터 공급원에서 직접 받은 캔들(Candle)이며, M1에서는 업비트(Upbit) 1분 캔들과 일봉을 의미한다.
_Avoid_: 원본 캔들, 공식 캔들

**파생 캔들(Derived Candle)**:
저장된 원천 캔들(Source Candle)을 집계해 만든 캔들(Candle)이며, 3분, 5분, 시간, 주, 월 단위 조회에 사용한다.
_Avoid_: 집계 캔들, 변환 캔들

**현재가 스냅샷(Ticker Snapshot)**:
특정 수집 시점에 관측한 최신 가격, 거래대금, 등락률, 관련 현재가 상태를 나타내는 시장 데이터다.
_Avoid_: 티커, 현재가, 실시간 가격

**호가 요약(Orderbook Summary)**:
특정 수집 시점의 최우선 매수/매도 가격과 수량, 스프레드(Spread), 상위 10호가 누적 잔량, 호가 불균형(Imbalance), 수집 지연을 나타내는 시장 데이터다.
_Avoid_: 호가, 오더북, 호가 스냅샷

**호가 원천 스냅샷(Source Orderbook Snapshot)**:
외부 데이터 공급원에서 받은 호가 단위(Orderbook Unit)를 요약하지 않고 특정 수집 시점 기준으로 보존한 시장 데이터다.
_Avoid_: 호가 요약, 호가, 원천 호가

**백필(Backfill)**:
신규 수집 대상 추가 또는 결측 복구를 위해 과거 시장 데이터를 채우는 수집 방식이다.
_Avoid_: 과거 수집, 초기 적재

**증분 수집(Incremental Collection)**:
이미 수집 중인 대상에 대해 새로 완성되거나 새로 관측된 시장 데이터만 이어서 수집하는 방식이다.
_Avoid_: 실시간 수집, 정기 수집

**후보 유니버스(Candidate Universe)**:
수집 대상으로 선택할 수 있는 후보 집합이며, M1에서는 업비트(Upbit) KRW 마켓 24시간 거래대금 기준 상위 100개를 의미한다.
_Avoid_: 종목 리스트, 후보 목록

**활성 수집 대상(Active Collection Target)**:
수집 워커(Collection Worker)가 실제로 데이터를 수집하는 대상 집합이며, M1에서는 사용자가 확정한 업비트 KRW 마켓 50개를 의미한다.
_Avoid_: 선택 종목, 수집 목록

**비활성 수집 대상(Inactive Collection Target)**:
과거 데이터는 보존하지만 현재 증분 수집(Incremental Collection)은 중단된 대상이다.
_Avoid_: 삭제 대상, 제외 종목

**수집 실행(Collection Run)**:
특정 데이터 유형과 시점에 대해 수집 워커(Collection Worker)가 수행한 작업 단위다.
_Avoid_: 작업, 배치 실행

**대상별 수집 결과(Target Collection Result)**:
하나의 수집 실행(Collection Run) 안에서 개별 수집 대상의 성공, 실패, 지연, 결측 상태를 기록한 결과다.
_Avoid_: 수집 로그, 결과 로그

**수집 시도 품질(Collection Attempt Quality)**:
수집 시도 자체의 성공, 실패, 응답 지연, 파싱 실패, 저장 실패를 나타내는 품질 관점이다.
_Avoid_: 수집 품질, 호출 품질

**데이터 완전성 품질(Data Completeness Quality)**:
기대한 시간 구간의 데이터 존재 여부, 중복, 최신성, 무데이터 가능 상태를 나타내는 품질 관점이다.
_Avoid_: 데이터 품질, 결측 품질

**수집 진행률(Collection Coverage)**:
수집 대상별 데이터 유형이 목표 수집 범위 대비 어느 시점 또는 어느 비율까지 수집됐는지를 나타내는 상태다.
_Avoid_: 진행률, 수집률, 백필 상태

**수집 버킷 시간(Collection Bucket Time)**:
시점성 시장 데이터를 정해진 저장 주기로 묶기 위해 사용하는 UTC 기준 시간 버킷이다. M1에서는 현재가 스냅샷(Ticker Snapshot)과 호가 요약(Orderbook Summary)을 분 단위로 대표 저장하기 위해 사용한다.
_Avoid_: 수집 시간, 기준 시간, 버킷

**결측 구간(Missing Range)**:
목표 수집 범위 안에서 기대한 시장 데이터가 아직 존재하지 않거나 복구가 필요한 시간 구간이다.
_Avoid_: 결측, 누락 구간, 빈 구간

**데이터 완전성 검사(Data Completeness Check)**:
목표 수집 범위와 저장된 데이터를 비교해 결측 구간(Missing Range)을 생성하거나 해결하는 작업이다.
_Avoid_: 품질 검사, 무결성 검사

**백필 계획(Backfill Plan)**:
백필(Backfill)을 실행하기 전에 대상, 기간, 예상 요청 수, 저장 예상량을 계산해 사용자가 승인할 수 있게 만든 계획이다.
_Avoid_: 백필 미리보기, 실행 계획

**백필 작업(Backfill Job)**:
사용자가 승인한 백필 계획(Backfill Plan)을 실제로 실행하고 상태와 진행률을 추적하는 작업이다.
_Avoid_: 백필 실행, 백필 태스크

**안전 재시작(Safe Restart)**:
기존 데이터를 삭제하지 않고 목표 범위 전체를 재검사해 없는 데이터만 다시 수집하는 백필 작업 재시작 방식이다.
_Avoid_: 재시작, 이어서하기

**삭제 후 재수집(Destructive Rebuild)**:
목표 범위의 기존 데이터를 삭제한 뒤 다시 수집하는 위험 작업이다.
_Avoid_: 초기화, 강제 재수집

**감사 로그(Audit Log)**:
운영 설정 변경, 수집 대상 변경, 백필 제어처럼 데이터 상태에 영향을 주는 쓰기 작업의 행위자, 시각, 대상, 변경 내용을 기록한 이력이다.
_Avoid_: 변경 로그, 작업 로그

**알림 이벤트(Notification Event)**:
수집 실패, 지연, 결측, 백필 실패처럼 사용자가 확인해야 하는 운영 상태 변화를 제품 안에 표시하기 위해 저장하는 이벤트다.
_Avoid_: 알림, 경고

**거래 상품(Instrument)**:
거래소나 시장에서 실제로 거래되는 단위이며, M1에서는 업비트(Upbit) KRW 마켓의 `KRW-BTC` 같은 마켓을 의미한다.
_Avoid_: 종목, 코인, 마켓 코드

**저장 시각(Storage Time)**:
DB와 API 계약에서 사용하는 UTC 기준 시각이다.
_Avoid_: 서버 시간, 저장 시간

**표시 시각(Display Time)**:
사용자 화면에서 시장 맥락에 맞춰 보여주는 시각이며, 업비트 KRW 마켓은 KST(Korea Standard Time)를 기본으로 한다.
_Avoid_: 로컬 시간, 화면 시간
