# STM32 펌웨어 (Nucleo-F401RE)

`stm32_main.c` 는 CubeMX 로 생성한 프로젝트의 `Core/Src/main.c` 를 통째로 교체할 수 있는
완성본이다. 서보 3개(Pan/Tilt/Trigger)를 TIM3 PWM 으로 제어하고, **USART1(PA9/PA10)** 로
Raspberry Pi 와 통신한다.

> ℹ️ Nucleo-F401RE 의 PA2/PA3(USART2)는 온보드 ST-LINK 가상COM(VCP)에 묶여 있어 외부
> UART 통신에 충돌이 난다. 그래서 **VCP 와 무관한 USART1(PA9=D8, PA10=D2)** 을 사용한다.

## 펌웨어 동작

- **TIM3 PWM 50Hz** (84MHz/84 = 1MHz tick, period 20000 → 20ms)
  - `_SERVO_CONTROL(num, angle)`: angle 0~180 → 펄스 500~2500 (0.5~2.5ms)
  - CH1 = **Pan**, CH2 = **Tilt**, CH3 = **Trigger**
- **USART1 115200 8N1**, PA9=TX, PA10=RX, 수신 인터럽트 기반 패킷 파서
- 시작 자세: Pan/Tilt = 90°, **Trigger = 120°(걸쇠 유지)**
- 발사: `trigger=1` 명령 수신 → Trigger 서보 **0°(걸쇠 해제, 시계방향 회전)** → `TRIGGER_HOLD_MS`(500ms)
  후 자동으로 120° 복귀(재장전 가능)
  - ※ 발사 회전 방향은 `TRIGGER_REST_DEG`(잠금)/`TRIGGER_FIRE_DEG`(발사) 값으로 결정.
    FIRE < REST 면 시계방향, FIRE > REST 면 반시계방향. 행정/각도는 이 두 값으로 조정.
- 50ms 주기로 현재 Pan/Tilt 상태를 Pi 로 전송

## UART 프로토콜 (Pi ↔ STM32)

```
Pi  -> STM32  명령 (5B): [0xAA][pan 0-180][tilt 0-180][trigger 0/1][checksum]
                          checksum = (pan + tilt + trigger) & 0xFF
STM32 -> Pi   상태 (4B): [0x55][pan_current][tilt_current][checksum]
                          checksum = (pan_current + tilt_current) & 0xFF
```

## CubeMX 필수 설정 ⚠️

1. **USART1 global interrupt 활성화** (NVIC Settings 탭에서 체크)
   - 이래야 `stm32f4xx_it.c` 의 `USART1_IRQHandler()` 가 생성되어
     `HAL_UART_IRQHandler(&huart1)` 를 호출하고, 수신 콜백이 동작한다.
   - (펌웨어에서도 방어적으로 `HAL_NVIC_EnableIRQ(USART1_IRQn)` 를 호출하지만,
     IRQ 핸들러 자체는 CubeMX 가 생성해야 한다.)
2. **TIM3 CH1/CH2/CH3** 를 PWM Generation 으로 설정 (핀은 CubeMX 매핑대로,
   일반적으로 PA6/PA7/PB0). `MX_TIM3_Init` 의 Prescaler=83, Period=19999 유지.
3. **USART1** Asynchronous, 115200, 8N1 (PA9=TX, PA10=RX 자동 할당).

## 배선

| STM32 | 보드 헤더 | 연결 |
|-------|----------|------|
| PA9 (USART1_TX) | D8 | → Pi RXD (GPIO15, 핀10) |
| PA10 (USART1_RX) | D2 | ← Pi TXD (GPIO14, 핀8) |
| GND | — | ↔ Pi GND (공통) |
| TIM3 CH1/CH2/CH3 | PA6/PA7/PB0 | → Pan/Tilt/Trigger 서보 신호선 |
| GND | — | ↔ 서보 전원 GND (공통) |

- **서보 전원**: MG996R 은 전류 소모가 크므로 **외부 5~6V 전원**으로 공급하고
  STM32 와 **GND 를 공통**으로 연결한다 (STM32 5V 핀으로 직접 구동 금지).
- **로직 레벨**: STM32·Pi 모두 3.3V TTL → UART 직결 가능(레벨 시프터 불필요).

> ✅ 이미 적용됨: Nucleo-F401RE 의 PA2/PA3(USART2)는 ST-Link VCP 와 연결돼 외부 통신이
> 충돌하므로, 이 펌웨어는 **USART1(PA9=D8, PA10=RX=D2)** 를 사용한다(VCP 무관). 따라서
> CubeMX 에서도 **USART1** 을 켜야 하며, Pi 배선은 위 표(PA9/PA10)대로 한다.
