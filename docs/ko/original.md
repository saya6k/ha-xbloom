# xBloom Original (J20)

Original은 로컬 제어 경로가 없는 Wi-Fi 머신입니다. 공식 앱도 기기와 직접
통신하지 않고, xBloom 클라우드에 **desired thing-shadow**를 올리고 같은
클라우드에서 보고 상태를 받아옵니다. 이 통합의 Original 지원은 블루투스나
LAN 클라이언트가 아니라 그 클라우드의 클라이언트입니다.

> **미검증.** 이 저장소에는 Original 실기와 실제 계정이 없습니다. 로그인
> 흐름·요청 서명·엔드포인트는 앱 2.2.5를 디컴파일해 얻었습니다
> ([`protocol-original-j20.md`](./protocol-original-j20.md) 참고). 실기에서
> 검증되기 전까지 아래 내용은 조정이 필요할 수 있습니다.

## 설정

1. 설정 → 기기 및 서비스 → **통합 추가** → **xBloom Coffee Machine**.
2. **Original (Wi-Fi / 클라우드)** 선택.
3. Original을 소유한 xBloom 앱 계정의 이메일/비밀번호 입력.
4. 계정에 Original이 여러 대면 하나를 선택.

자격증명은 클라우드 토큰이 만료됐을 때 다시 로그인할 수 있도록 config
entry에 저장됩니다. 항목을 삭제하면 함께 삭제됩니다.

## 엔티티

| 엔티티 | 의미 |
|---|---|
| `binary_sensor.…_online` | thing shadow의 `is_online` |
| `binary_sensor.…_problem` | `warning`이 0이 아닌 값으로 설정됨 |
| `binary_sensor.…_grinding` | `grinding_start` (shadow에 있을 때) |
| `binary_sensor.…_calibrating` | `auto_calibrate` (shadow에 있을 때) |
| `sensor.…_state` | `work_mode`를 idle/awake/ready/grinding/brewing/complete/sleeping으로 매핑 |
| `sensor.…_warning` | `warning`을 분쇄/물/도크/원두/전압으로 매핑 |
| `sensor.…_brew_progress` | `disp_reserve` |
| `sensor.…_pod` | `capsule_rfid_tag`; `IF0002`는 “팟 없음” |
| `sensor.…_power_mcu` / `…_panel_mcu` | MCU별 펌웨어 버전 |
| `button.…_grind` | 현재 분쇄도(1–30)로 분쇄 시작 |
| `button.…_pour` | 현재 물량/온도/패턴으로 수동 추출 |
| `button.…_stop` | 추출 중지 (`bruw_curve: "FFFF11"`) |
| `button.…_calibrate` | 그라인더 자동 영점 (`auto_calibrate: 1`) |
| `number.…_grind_size` | 분쇄 버튼이 보낼 분쇄도 (1–30, 기본 15) |
| `number.…_volume` | 추출 물량 ml (30–500, 기본 225) |
| `number.…_temperature` | 추출 온도 °C (85–96) |
| `select.…_pattern` | 추출 패턴: center / circular / spiral |

수동 추출은 앱의 정확한 인코딩을 사용합니다:
`FFFF1005` + 물량(hex 4) + 온도(hex 2) + `1E` + 패턴(중앙 `00` / 원형 `01` /
나선 `02`). 물량 30–500 ml(기본 225), 온도 85–96 °C(기본 85)이며 양 끝은
고정 RT 85 / BP 95 상수로 보냅니다.

폴링 주기는 기본 15초이며, 항목 옵션에서 5–300초로 바꿀 수 있습니다.

## 구현되지 않은 것

- **레시피.** 저장된 레시피 시작은 서버가 생성하는 `getRecipeMadeCmd`
  페이로드를 거칩니다. 수동 추출과 중지만 노출합니다.
- **WebSocket.** 앱은 `wss://…execute-api…/prod/`로 실시간 델타를 받지만,
  구독/인증 핸드셰이크가 문서화되지 않아 이 통합은 thing shadow를
  폴링합니다.
- **로컬 탐색.** 포트 스캔이나 LAN 제어는 시도하지 않았습니다. 펌웨어는
  폐쇄되어 있고 기기는 클라우드에 묶여 있습니다.
- **서비스 / LLM 도구.** J20 항목은 위 엔티티만 노출하며, Studio의
  서비스와 Assist 도구는 연결되지 않습니다.

## 문제 해결

- **“이 계정에서 xBloom Original을 찾지 못했습니다.”** 계정에 J20이 없거나
  `tuMachineList.tuhtml` 응답 형식이 예상과 다릅니다. HA 로그에서
  `j20/client.py`의 경고를 확인하세요.
- **이메일/비밀번호 오류.** Shopify 스토어 계정이 아니라 xBloom 앱 로그인을
  사용하세요.
- **모든 값이 `unknown`.** shadow 요청이 실패한 것입니다. 로그에서
  `XBloom Original shadow failed` 또는 네트워크 경고를 찾으세요.
- **명령 타임아웃.** 클라우드가 서명 헤더를 거부했을 수 있습니다. `sign`은
  `MD5(appid,appsecret,nonce,ts)` 대문자이며 `signed_headers()`를
  참고하세요.

## 왜 로컬이 아닌가

[`protocol-original-j20.md`](./protocol-original-j20.md) 참고 — 기기의
비클라우드 경로는 공장 Wi-Fi 프로비저닝 BLE 링크뿐이고, 바인딩 후
끊깁니다. 그 페이로드의 `activate_url`은 클라우드가 발급하는 일회성
활성화 엔드포인트이지, 제어용 서버 선택자가 아닙니다.
