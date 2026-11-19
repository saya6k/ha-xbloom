# xBloom Original (J20) 클라우드 프로토콜 레퍼런스

> 이 페이지는 [`en/protocol-original-j20.md`](../en/protocol-original-j20.md)의
> 번역본입니다. 영문판이 source of truth이며, 한글본은 뒤늦게 동기화될 수
> 있습니다. 이 문서는 전적으로 공식 안드로이드 앱
> (`xbloom_coffee_release.apk`, 패키지 `com.chisalsoft.andite`)을 `jadx`로
> 정적 디컴파일한 결과(zip/dex/소스 덤프로만 확인 — 배포·재배포된 적 없음)를
> 바탕으로 작성되었습니다. **[`protocol.md`](./protocol.md)(Studio BLE)와 달리,
> 여기 내용은 실기로 검증된 것이 하나도 없습니다.** 이제 이 통합에는
> `custom_components/xb/j20/`의 클라우드 전용 J20 경로(config entry 유형
> `original`: 계정 로그인, thing shadow 폴링, 분쇄/중지/캘리브레이션)가
> 포함되어 있지만, 이 저장소에는 여전히 Original 실기와 실제 계정이 없어
> 그 코드도 미검증입니다. 이 문서는 그 구현의 근거가 되는 디컴파일 기반
> 지도이며, 모든 주장은 특정 클래스/메서드로 추적 가능하고 교차 확인되지
> 않은 내용은 unconfirmed로 표시했습니다.

## Original이 별도 문서인 이유

앱은 내부적으로 두 기기를 `DeviceType` 정수로 구분합니다
(`constant/DeviceType.java`):

| `DeviceType` | 값 | 제품 | `productId` | 전송 방식 |
|---|---|---|---|---|
| `DeviceJ15` | `1` | **Studio** | — | Bluetooth LE ([`protocol.md`](./protocol.md) 참조) |
| `DeviceJ20` | `2` | **Original** | `"53DDY4"` | Wi-Fi + 클라우드 IoT thing-shadow |

`DeviceJ15 == 1`은 클라우드 API에 하드코딩된 `adaptedModel: 1`(Studio)과
일치합니다. 두 기기는 전송 계층에서 공유하는 것이 거의 없습니다 — 모든 J20
화면, 스캔 플로우, 네트워킹 클래스가 Studio 쪽의 병렬 `*J20*` 형제입니다
(`MachineJ20Fragment`, `PairDeviceJ20Activity`, `BrewerJ20Activity`,
`GrinderJ20Activity`, `AutoJ20Activity`, `J20ScanVM`, …). **Original에는 BLE
brew/grind/pour 명령 경로가 아예 없습니다** — 이 통합의 BLE 스택 중 재사용
가능한 것이 왜 하나도 없는지는 맨 아래 "Studio BLE 프로토콜과의 관계" 참조.

## 아키텍처: Wi-Fi + AWS IoT thing shadow

Original은 직접 제어가 아니라 xBloom 클라우드를 통해 제어되는 Wi-Fi
가전입니다. 전형적인 AWS IoT **thing shadow**(디바이스 섀도우) 패턴입니다:

1. 앱은 기기에 brew/grind 명령을 직접 보내지 않습니다. **원하는 상태(desired
   state)** 문서를 클라우드로 `POST`합니다(`/api/device/command`).
2. 클라우드가 그 desired 상태를 자체 IoT/MQTT 링크로 기기에 전달합니다.
3. 기기는 **보고 상태(reported state)**를 되돌려주고, 클라우드는 이를
   **WebSocket**으로 `state.reported.{...}` JSON 형태로 앱에 팬아웃합니다.

즉 Original용 HA 통합은 Bluetooth 클라이언트가 아니라 **클라우드
폴링/스트리밍 클라이언트**가 됩니다 — `ble/`나 `coordinator/connection.py`보다
`_cloud_client.py`에 훨씬 가까운 형태입니다.

## 호스트와 인증

IoT 백엔드 베이스 URL은 `ServiceConfig`(글로벌 vs 중국)가 선택하며,
`AboutActivity`가 글로벌 기본값을 확인해 줍니다:

- **글로벌**: `https://api-iot.xbloom.com/`
- **중국**: `https://api-iot.xbloomcoffee.cn/`
- **WebSocket(글로벌)**: `wss://15qmk9hof3.execute-api.us-east-1.amazonaws.com/prod/`
  (`ConfigBean`); 중국: `wss://lx2wz1fksl.execute-api.cn-north-1.amazonaws.com.cn/prod/`

인증은 앱의 나머지 부분이 쓰는 것과 동일한 서명 헤더 스킴입니다
(`cn.com.library.net.RetrofitManager`, 공유 OkHttp 클라이언트의 인터셉터
— J20의 `ApiDevice`도 같은 매니저를 탑니다):

```
platform: android
appid: 94a98bca981d11eb9c0824418c29779a
version: 2.2.5
accept-language: en
ts: <epoch 초>                 # currentTimeMillis / 1000
nonce: <대시 없는 uuid4, 소문자>
sign: <MD5(appid,secret,nonce,ts) 대문자 hex>
Authorization: token <projectToken>
```

secret은 `de77d4d97ecf9003eaa5f372fd9a616b`(`ServiceConfig.configBeanRelease`)이며,
서명 입력은 `appId,appSecret,nonce,ts`를 콤마로 이은 문자열입니다. 로그인은
`client-api.xbloom.com`의 `tMemberLogin.thtml`이고, 응답에는 `token`(레시피 API),
`projectToken`(이 헤더의 bearer), `projectRefreshToken`이 들어 있습니다.

`api-iot` 호출이 `code: 10001`을 돌려주면 `TokenInterceptor`가
`PUT /api/enduser/refresh_token/`을 `{"refresh_token": <projectRefreshToken>}`로
호출합니다. `code: 0` 응답의 `data`에 새 `access_token` / `refresh_token`이
들어 있고, 원래 요청을 새 토큰으로 다시 보냅니다. 앱은 refresh token이 없거나
갱신이 실패할 때만 로그아웃합니다.

## 온보딩 / 페어링 (Original이 BLE를 쓰는 유일한 곳)

Original에서 BLE는 딱 한 번 등장합니다: 공장 초기 상태의 기기에 Wi-Fi
자격증명을 넘겨줄 때입니다. `SetWifiActivity`가 SSID + 비밀번호를 수집한 뒤,
`PairDeviceJ20Activity.startConn()`이 이 앱 자체 BLE 스택이 **아니라** MXChip의
lock SDK(`com.mxchip.locklib.BleLockManager`)로 그것을 BLE로 밀어 넣습니다:

```java
// PairDeviceJ20Activity.java ~207행
WifiBean wifiBean = new WifiBean(
    new WifiInfoBean(wifiSSID, "", "", wifiPassword, token), null, 2, null);
BleLockManager.getInstance().connect(retrofit, deviceFindBean.getBean(), wifiBean, cb);
```

기기가 Wi-Fi에 붙은 뒤 IoT 백엔드에 인증할 수 있도록 클라우드 `token`이
자격증명과 함께 번들됩니다. 프로비저닝 후 BLE는 다시는 사용되지 않으며, 모든
제어는 클라우드를 경유합니다.

저 두 빈 문자열은 **생성자 자리표시자일 뿐, 실제 전송 값이 아닙니다.**
`BleLockManager.AnonymousClass3`는 JSON 직렬화 직전에 두 필드를
SharedPreferences 값으로 덮어쓰고(`setActivate_url(SP.ACTIVATION_URL)` /
`setBinding_code(SP.BIND_CODE)`), 그 값들은 `POST /api/device/bind/code`
응답의 `BindDeviceInfoBean.activation_url` / `.bind_code`에서 기록됩니다
(`LockViewModel`). 즉 `activate_url`은 앱이나 사용자가 고르는 서버가 아니라
**클라우드가 발급하는 일회성 activation/binding 엔드포인트**이며, 지속적인
제어 채널도 아닙니다 — 제어는 항상 아래의 thing-shadow 엔드포인트로 갑니다.
커스텀 activation URL은 잘해야 일회성 activation 호출만 가로챌 뿐, 기기의
제어 경로를 바꾸지 못합니다.

### 전송 프로토콜(MXChip `locklib`), `models/j20/provision.py`에 구현

블루투스 검색에서 Wi-Fi 설정을 기다리는 Original을 찾으면 Home Assistant가 이
온보딩을 직접 진행합니다(통합 구성요소 추가가 필요 없음). **실제 하드웨어에서
검증하지 않았습니다.**

- **광고**: 16비트 서비스 `0x0922`. 제조사 데이터(회사 ID 뒤)는 1바이트, MAC(6),
  제품 ID `53DDY4`(ASCII 6). MAC은 클라우드에 소문자 16진수 12자리로 보냅니다.
- **GATT**: 서비스 `00000922-…`, 쓰기 `0000aab1-…`, 알림 `0000aab2-…`.
- **분할**: 19바이트 조각 앞에 헤더 바이트 — `0x00` 메시지 전체, `0x01` 처음,
  `0x10` 중간, `0x11` 마지막(양방향 동일).
- **절차**: `POST /api/device/bind/code` → `bind_code`, `activation_url`;
  `POST /api/device/ble/encryption/random/` → `random`, `random_key`;
  BLE `{"type":"random","data":{"random"}}` → 기기가 `data.cipher`로 응답;
  `POST /api/device/ble/encryption/verify/` {mac, product_id, cipher,
  random_key} → `ble_key`; BLE `{"type":"cipher_reply","data":{"error_code":0}}`;
  BLE `WifiBean` `{"data":{ssid, activate_url, binding_code, password,
  user_token}, "type":"apinfo"}`을 AES-128-CBC/PKCS7(키 = 16진수 `ble_key`,
  IV = ASCII `0` 16개)로 암호화해 전송; 기기는 암호화한
  `{"type":"wifi_reply","data":{"error_code"}}`의 base64로 응답(0 성공, 1 네트워크
  없음, 2 신호 약함, 3 접속 실패). 10초 뒤 `POST /api/device/bind/status`
  {mac, product_id, bind_code}를 3초마다(10회) `data.device_id`가 나올 때까지
  호출. `user_token`은 IoT 액세스 토큰(`projectToken`). 마지막으로 앱은
  `tuBindMachine.tuhtml`로 계정의 기기 목록에 추가합니다.

## 펌웨어 / OTA

**앱에서 Original 펌웨어를 내려받는 경로는 없습니다.** `.bin` 업그레이드
경로 — `tUpToDateFirmwareVersion.thtml` → `resourceLinks` → 로컬
`FW_J15<version>.bin` → `AppBleManager.initYModem` → BLE `8101` — 는
`MachinesActivity.getUpToDateFirmwareVersionTransfer()`의 `device.isJ15()`
가드에 걸려 있고, `MachineAdvancedFeaturesJ15Activity` / `FwUpgradeActivity`
에서만 도달할 수 있습니다. J20 쪽에는 업그레이드 화면도 OTA 호출도 없고,
thing shadow의 `multimcuotainfo.mcu_info[].{mcu_name, mcu_version}`
(PowerMCU / PannelMCU)를 읽어 `J20Firmware.isLegacyFirmware()`로 구형 펌웨어의
기능만 숨깁니다. 따라서 Original 펌웨어 업데이트는 기기/클라우드 주도이며,
앱의 업그레이드 경로로 Original 이미지를 받아낼 수는 없습니다.

## REST 엔드포인트 (`http/j20/ApiDevice.java`)

```
POST /api/device/command        body: DeviceCommandReq          → BaseResp<Object>
GET  /api/device/detail/        query: device_id                → BaseResp<DeviceJ20InfoDetailModel>
POST /api/device/thing_shadow/  body: { device_id, product_id } → BaseResp<DeviceJ20DetailModel>
```

- **`/command`** — desired-state 섀도우 업데이트 푸시(쓰기 경로).
- **`/detail/`** — 일회성 기기 메타데이터(`sn`, `device_id`).
- **`/thing_shadow/`** — 전체 현재 섀도우(보고 상태 읽기 경로,
  `deviceFullProperties`); WebSocket이 실시간 갱신을 넘겨받기 전 초기 스냅샷용.
  `/command`와 달리 `product_id`로 기기 목록의 해당 기기 `productId`를
  보냅니다(`getJ20Info`).

## 명령 모델 (쓰기 경로)

`req/DeviceCommandReq.java` — `/api/device/command`의 POST 본문:

```
DeviceCommandReq {
  device_id:  <device.deviceId>
  product_id: "53DDY4"            // DeviceType.getJ20ProductId(), 상수
  desired:    <섀도우 문서>        // 아래 참조
  home_id:    null                // 모든 호출 지점에서 null 관측
}
```

`desired` 객체는 중첩된 AWS-IoT 섀도우 문서입니다 —
`model/Desired.java` → `State` → `BaseDesiredInside` — 다음으로 직렬화됩니다:

```json
{ "state": { "desired": { <아래 *DesiredInside 페이로드 중 하나> } } }
```

확인된 페이로드 형태(각 `*DesiredInside`는 `BaseDesiredInside`를 상속)와 이를
구성하는 호출 지점:

| 동작 | Desired 페이로드 | 구성 위치 |
|---|---|---|
| **분쇄(Grind)** | `{ "grinding_start": 1, "setting_grind_size": <1–30, 기본 15> }` | `GrinderJ20Activity` L374 |
| **그라인더 캘리브레이션**(오토 제로) | `{ "auto_calibrate": 1 }` | `CalibrateGrinderJ20Activity` L84 |
| **추출(Pour/brew)** | `{ "bruw_curve": "FFFF1005<vol:4><temp:2>1E<pattern>" }` | `BrewerJ20Activity` L852 |
| **급수 정지** | `{ "bruw_curve": "FFFF11" }` | `BrewerJ20Activity` L878 |

참고:

- `setting_grind_size`는 UI 텍스트 필드에서 바로 파싱한 분쇄 숫자입니다.
  `CoffeeConstantUtil.getGrindSizeJ20`가 시크바를 **1–30, 기본 15**로
  고정합니다(Studio의 1–80과 다름).
- `bruw_curve`(sic — 앱 자체의 "brew curve" 오타)는 구조체가 아니라 hex
  문자열입니다. `BrewerJ20Activity.startWater`가 정확히
  `"FFFF1005" + volumeHex4 + temperatureHex2 + "1E" + patternCode`로 만듭니다:
  - volume은 **ml** 시크바 값, 범위 **30–500**, 기본 **225**, 대문자 hex로
    4자리 zero-pad;
  - temperature는 **°C** 시크바 값, 범위 **85–96**, 기본 **85**; 85 이하이면
    고정 `RTJ20 = 85`, 96 이상이면 고정 `BPJ20 = 95`, 그 사이는 정수값을
    대문자 hex 2자리 zero-pad;
  - `"1E"`는 고정 리터럴 30;
  - 패턴: 중앙 `"00"`, 원형 `"01"`, 나선 `"02"`.
  예: 225 ml, 93 °C, 중앙 → `FFFF100500E15D1E00`.
  `"FFFF11"`은 정지 센티넬입니다.
- `home_id`는 data 클래스 필드지만 추적된 모든 호출 지점에서 null입니다.

## 상태 / 텔레메트리 (읽기 경로)

`manager/AppWsManager.java`가 WebSocket을 열고 각 프레임의 `state.reported`
객체를 파싱하여 필드들을 앱의 `RxBus`로 재방송합니다. 이것이 처리하는 보고
필드들(사실상의 Original 텔레메트리 스키마):

| `state.reported.*` | 의미 | 비고 |
|---|---|---|
| `is_online` | 기기 온라인 플래그 | → `DeviceJ20OnlineEvent` |
| `work_mode` | 전원/깨어남 + 활동 상태 | `0` 유휴, `1` 깨어남, `4/7/11/13` 분쇄, `5` 준비(팟 삽입), `6/8/14/15` 추출, `9` 절전, `10/12` 완료 |
| `warning` | 오류/경고 코드 | `162/176` 분쇄 설정 경고, `169` 물 부족, `171` xPod 도크 경고, `182` 원두 없음, `190` 급수 부족, `191–193` 전압 불일치; `166/183/185`는 앱이 무시 |
| `grinding_start` | 그라인더 동작 상태 | → `DeviceJ20GrinderStartEvent` |
| `auto_calibrate` | 캘리브레이션 상태 | 캘리브레이션 명령 필드와 대칭 |
| `capsule_rfid_tag` | 삽입된 팟의 RFID/`xid` | `"IF0002"`는 "팟 없음"으로 취급, 다른 값은 팟 상세 페이지를 엶 |
| `disp_reserve` | 급수/추출 진행도 | `<3` 추출 진행(0/1/2), `3` 레시피 변경됨, `4` Easy-Mode 레시피 시작, `6` 유휴/무시 |

- 보고 상태가 신뢰할 수 있는 상태 소스입니다 — Studio의 raw 상태 하트비트 vs
  cmd-tagged 프레임 교훈과 동일(`[[xbloom-raw-state-heartbeat-vs-cmd-tagged]]`).
- `thing_shadow/`가 초기 전체 스냅샷을, WebSocket이 델타를 제공합니다.
- 대응 모델: `model/DeviceJ20DetailModel.java`(`capsule_rfid_tag`, `is_online`,
  `warning`, `work_mode`, `DM_SN`, `multimcuotainfo` → MCU별
  `mcu_name`/`mcu_version`), `DeviceJ20Model.java`(`product_id`, `mac`,
  `device_id`, `enduser_id`, `sn`).

## Studio BLE 프로토콜과의 관계 — 아무것도 이식되지 않음

기존 코드 경로에 플래그 하나로 Original을 얹고 싶은 사람을 위해: 두 기기는
**전송 표면을 전혀 공유하지 않습니다**.

| 항목 | Studio (J15, 이 통합) | Original (J20) |
|---|---|---|
| 링크 | HA Bluetooth 스택 경유 BLE | Wi-Fi, 클라우드 경유 |
| 프레이밍 | `0x58 0x02` 패킷, CRC, MTU `8100` 핸드셰이크 | HTTPS + WebSocket 위 JSON |
| 명령 테이블 | `APP_*`/`RD_*` id, type-1/type-2 마커 | 섀도우 `desired` 문서 |
| 상태 | raw BLE 상태 하트비트 | 클라우드 `state.reported` 푸시 |
| 인증 | 없음(로컬 BLE) | 요청마다 서명된 클라우드 토큰 |
| HA 빌딩블록 | `bluetooth` 매처 + `coordinator/connection.py` | 클라우드 스트림 클라이언트(`thing_shadow` 폴링 + WS의 `DataUpdateCoordinator`) |

따라서 Original 지원은 사실상 레시피 스토어와 `_cloud_client.py` 스타일의 HTTP
배관만 공유하는 **두 번째 클라우드 전용 통합**입니다 — BLE 코디네이터에 얹는
작은 분기가 아닙니다. 현재 `j20/`의 별도 config-entry 유형으로 구현되어
있으며, 이 문서는 그 구현의 디컴파일 기반 지도입니다.

## 미해결 질문 (Original 하드웨어 없이는 막힘)

- WebSocket URL, 서브프로토콜, subscribe/인증 핸드셰이크, 재연결/ping 주기
  (`WsManager`는 벤더 라이브러리 `com.mixchip.websocketclient`).
- Tea 레시피가 J20 desired-state 형태를 갖는지 여부.
