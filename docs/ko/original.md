# xBloom Original (J20)

Original은 로컬 제어 경로가 없는 Wi-Fi 머신입니다. 공식 앱도 기기와 직접
통신하지 않고, xBloom 클라우드에 **desired thing-shadow**를 올리고 같은
클라우드에서 보고 상태를 받아옵니다. 이 통합의 Original 지원은 블루투스나
LAN 클라이언트가 아니라 그 클라우드의 클라이언트입니다.

> **실험적.** 이 저장소에는 Original 실기와 실제 계정이 없습니다. 로그인
> 흐름·요청 서명·엔드포인트는 앱 2.2.5를 디컴파일해 얻었습니다
> ([`protocol-original-j20.md`](./protocol-original-j20.md) 참고). 실기에서
> 검증되기 전까지 아래 내용은 조정이 필요할 수 있습니다.

## 설정

1. 설정 → 기기 및 서비스 → **통합 추가** → **xBloom Coffee Machine**.
2. **Original (Wi-Fi / 클라우드)** 선택.
3. Original을 소유한 xBloom 앱 계정의 이메일/비밀번호 입력.
4. 계정에 Original이 여러 대면 하나를 선택.

### 새 기기(첫 Wi-Fi 설정)

아직 설정하지 않은 Original은 블루투스로 Wi-Fi 설정을 기다립니다. Home
Assistant의 블루투스 범위 안에 있으면 **발견됨**에 *xBloom Original*로
나타납니다. 구성을 눌러 xBloom 계정과 Wi-Fi 네트워크(2.4GHz만)를 입력하면 Home
Assistant가 앱과 같은 일을 합니다. 기기에 Wi-Fi 설정을 보내고, 기기가 계정에
연결되기를 기다린 뒤, 계정의 기기 목록에 추가하고 항목을 만듭니다. 앱의 온보딩
프로토콜([`protocol-original-j20.md`](./protocol-original-j20.md) 참고)을
사용하며, 실제 기기에서는 아직 시험하지 않았습니다.

클라우드가 토큰을 거부하면 앱과 마찬가지로 먼저 refresh token으로 새
토큰을 받습니다. 자격증명은 그마저 실패했을 때 다시 로그인하기 위해서만
config entry에 저장됩니다. 항목을 삭제하면 함께 삭제됩니다.

shadow가 기기를 오프라인으로 보고하는 동안에는 `binary_sensor.…_online`을
제외한 모든 엔티티가 사용 불가가 되고, 버튼은 아무것도 보내지 않습니다(앱도
시작 버튼을 비활성화합니다). 이때 쓴 명령은 shadow에 남아 있다가 기기가 다시
연결되면 실행되기 때문입니다.

## 엔티티

| 엔티티 | 의미 |
|---|---|
| `binary_sensor.…_online` | thing shadow의 `is_online` |
| `binary_sensor.…_problem` | 앱이 표시하는 `warning` (앱처럼 166/183/185는 무시) |
| `binary_sensor.…_grinding` | `work_mode`가 4/7/11/13 (앱의 `isJ20Grinding`) |
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
| `select.…_recipe` | 실행 버튼이 추출할 저장된 레시피 |
| `button.…_execute_recipe` | 선택한 레시피 실행 — 첫 번째 누름에 기기를 확인하고 실행을 준비하며, 두 번째 누름에 시작 |

수동 추출은 앱의 정확한 인코딩을 사용합니다:
`FFFF1005` + 물량(hex 4) + 온도(hex 2) + `1E` + 패턴(중앙 `00` / 원형 `01` /
나선 `02`). 물량 30–500 ml(기본 225), 온도 85–96 °C(기본 85)이며 양 끝은
고정 RT 85 / BP 95 상수로 보냅니다.

폴링 주기는 기본 15초이며, 항목 옵션에서 5–300초로 바꿀 수 있습니다.

## 레시피

Original도 Studio처럼 자기 로컬 레시피 저장소를 가집니다. 처음 설정할 때
xBloom 계정의 레시피로 한 번 채워지고, 패널·`xb.*` 서비스·대화 에이전트에서
레시피를 만들고, 고치고, 공유 링크나 커뮤니티 허브에서 가져오고, 내보낼 수
있습니다. Original에는 차 추출기가 없어서 차 레시피는 빠집니다.

레시피 실행은 앱(`RecipeDetailActivity`)을 따릅니다(실기 미검증):

1. **확인.** 기기가 온라인이고, 물 경고가 없고, 추출 준비 상태(`work_mode`
   5)이거나 최신 펌웨어에서 막 깨어난 상태(1)여야 합니다. 구형
   펌웨어(MCU 버전이 3.0.0–8.0.0 밖)에서는 분쇄를 건너뛰는 레시피를
   거부합니다.
2. **준비.** xBloom 클라우드가 레시피(이름, 분쇄 굵기, 비율, 원두량,
   추출 단계)를 기기 실행 명령으로 바꿉니다(`tGetRecipeMadeCmd.thtml`).
3. **시작.** 두 번째 누름(또는 `confirmed=true`) 뒤 그 명령을 그대로 기기에
   보냅니다(`POST /api/device/command`). 이후 분쇄·추출·완료가 상태 센서에
   나타납니다.

`xb.execute_recipe` 서비스는 세 단계를 한 번에 합니다. 이번 추출만의
변경은 Studio처럼 원두량, 비율, 온도, 개별 추출 단계에 쓸 수 있습니다.
Original의 실행 명령에는 바이패스 물이 없고, 레시피의 분쇄 굵기는 저장된
값 그대로 보내므로(클라우드가 Original의 1–30 단계로 바꿈) `grind_size`/`rpm`
변경은 적용되지 않습니다.

## 사이드바 패널과 음성 / 채팅 에이전트

xBloom 사이드바 패널은 Original도 Studio와 함께 보여주며, **추출**,
**레시피**, **수동**, **설정** 탭이 있습니다. 추출 탭은 선택한 레시피를 두
번 눌러(준비 후 시작) 실행하고, 분쇄와 추출 진행을 따라갑니다. Easy 슬롯과
저울은 Studio에서만 보입니다.

Original에도 대화 에이전트 설정에서 고를 수 있는 LLM API("XBloom Coffee
Machine (<시리얼>)")가 생깁니다. 기기가 지원하는 도구만 제공합니다: 상태,
레시피 도구(목록, 조회, 생성, 수정, 삭제, 실행, 가져오기, 내보내기, 커뮤니티
검색), `xbloom__grind`(1–30), `xbloom__pour`(85–95 °C 또는 끓는 물, 30–500
ml), `xbloom__cancel`, `xbloom__calibrate_grinder`. 레시피·분쇄·추출은
Studio처럼 먼저 확인을 받습니다. Original에는 "대기(arm)" 화면이 없어서
확인된 호출 전에는 기기에 아무것도 보내지 않습니다. `xbloom__cancel`은
추출은 멈추지만, 진행 중인 분쇄는 멈출 수 없습니다(앱에도 그런 명령이
없습니다).

## 구현되지 않은 것

- **Easy 모드 / 자동 모드.** 앱은 레시피 하나를 기기 자체 시작 버튼에
  저장할 수도 있습니다(`tuAutoMode*.tuhtml`). 구현하지 않았습니다.
- **WebSocket.** 앱은 `wss://…execute-api…/prod/`로 실시간 델타를 받지만,
  구독/인증 핸드셰이크가 문서화되지 않아 이 통합은 thing shadow를
  폴링합니다.
- **로컬 탐색.** 포트 스캔이나 LAN 제어는 시도하지 않았습니다. 펌웨어는
  폐쇄되어 있고 기기는 클라우드에 묶여 있습니다.

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
