# xBloom Original (J20)

Original은 로컬 제어 경로가 없는 Wi-Fi 머신입니다. 공식 앱도 기기와 직접
통신하지 않고, xBloom 클라우드에 **desired thing-shadow**를 올리고 같은
클라우드에서 보고 상태를 받아옵니다. 이 통합의 Original 지원은 블루투스나
LAN 클라이언트가 아니라 그 클라우드의 클라이언트입니다.

> **상태 보고 전용(실험적).** Home Assistant는 Original의 상태를 보여 줄 뿐
> 아직 조작하지는 못합니다. 조작 기능(분쇄, 추출, 중지, 분쇄기 캘리브레이션,
> 저장된 레시피, 시작 버튼 자동 모드, 블루투스를 통한 첫 Wi-Fi 설정)은 공식
> 앱 코드를 바탕으로 구현했지만 실제 기기에서 한 번도 실행된 적이 없어서,
> 릴리스에서 빼고 저장소의
> [`original`](https://github.com/saya6k/ha-xbloom/tree/original) 브랜치에
> 두었습니다. Original을 가지고 있고 시험을 도와줄 수 있다면
> [이슈 #232](https://github.com/saya6k/ha-xbloom/issues/232)를 봐 주세요.
>
> 로그인 흐름·요청 서명·엔드포인트는 앱 2.2.5를 디컴파일해 얻었습니다
> ([`protocol-original-j20.md`](./protocol-original-j20.md) 참고).

## 설정

1. 먼저 공식 xBloom 앱으로 기기를 설정하세요(Wi-Fi와 계정).
2. 설정 → 기기 및 서비스 → **통합 추가** → **xBloom Coffee Machine**.
3. **Original (Wi-Fi / 클라우드, 상태만)** 선택.
4. Original을 소유한 xBloom 앱 계정의 이메일/비밀번호 입력.
5. 계정에 Original이 여러 대면 하나를 선택.

클라우드가 토큰을 거부하면 앱과 마찬가지로 먼저 refresh token으로 새 토큰을
받고, 그것도 실패할 때만 다시 로그인합니다. 자격 증명은 그 용도로만 config
entry에 저장되며, 항목을 삭제하면 함께 지워집니다.

기기에는 아무것도 쓰지 않습니다. 통합은 로그인하고, 계정의 기기 목록을
읽고, thing shadow를 읽기만 합니다.

shadow가 기기를 오프라인으로 보고하는 동안에는 실시간 값(상태, 경고, 문제,
분쇄 중, 추출 진행, 팟)이 **알 수 없음**으로 표시됩니다. shadow에는 마지막
보고가 남아 있지만 현재 상태가 아니기 때문입니다. 엔티티는 xBloom 클라우드에
아예 닿지 않을 때만 사용 불가가 됩니다.

## 엔티티

| 엔티티 | 의미 |
|---|---|
| `binary_sensor.…_online` | thing shadow의 `is_online` |
| `binary_sensor.…_problem` | 앱이 보여 줄 `warning`(166/183/185는 앱처럼 무시) |
| `binary_sensor.…_grinding` | `work_mode`가 4/7/11/13(앱의 `isJ20Grinding`) |
| `sensor.…_state` | `work_mode` → idle/awake/ready/grinding/brewing/complete/sleeping |
| `sensor.…_warning` | `warning` → 분쇄/물/도크/원두/전압 |
| `sensor.…_brew_progress` | `disp_reserve` |
| `sensor.…_pod` | `capsule_rfid_tag`; `IF0002`는 “팟 없음”으로 처리 |
| `sensor.…_power_mcu` / `…_panel_mcu` | MCU별 펌웨어 버전 |

실시간 변경은 앱의 WebSocket
(`wss://15qmk9hof3.execute-api.us-east-1.amazonaws.com/prod/`, `Authorization`
헤더에 IoT 토큰, 30초마다 ping)으로 들어오며, 서버는 바뀐 필드만 담은
`{device_id, state: {reported: {…}}}`를 보냅니다. 그 아래에서 폴링이 대비책으로
계속 돌며, 간격은 기본 15초이고 항목 옵션에서 바꿀 수 있습니다(5–300초).

## 사이드바 패널과 음성 / 채팅 에이전트

xBloom 사이드바 패널은 Original을 Studio와 함께 보여 줍니다. **설정** 탭에
기기 상태와 앱의 디스케일링 단계가 나옵니다(디스케일링은 앱에도 명령이
없으며, 기기 뒷면 버튼으로 합니다).

Original은 기기 이름과 같은 LLM API("xBloom Original <일련 번호 끝 여섯
자리>")도 따로 가지며, 대화 에이전트 설정에서 고를 수 있습니다. 제공하는
도구는 `xbloom__get_status`뿐입니다.

## 문제 해결

- **“이 계정에서 xBloom Original을 찾을 수 없습니다.”** 계정에 J20이 없거나
  `tuMachineList.tuhtml`이 예상과 다른 형태를 돌려줬습니다. HA 로그에서
  `j20/client.py`의 경고를 확인하세요.
- **이메일/비밀번호가 틀림.** Shopify 스토어 계정이 아니라 xBloom 앱 계정으로
  로그인하세요.
- **모든 값이 계속 `알 수 없음`.** 기기가 오프라인이거나 shadow 요청이
  실패하고 있습니다. 로그에서 `XBloom Original shadow failed`나 네트워크
  경고를 찾아보세요.

## 왜 로컬이 아닌가

[`protocol-original-j20.md`](./protocol-original-j20.md) 참고 — 기기의
비클라우드 경로는 공장 Wi-Fi 프로비저닝 BLE 링크뿐이고, 바인딩 후
끊깁니다. 그 페이로드의 `activate_url`은 클라우드가 발급하는 일회성
활성화 엔드포인트이지, 제어용 서버 선택자가 아닙니다.
