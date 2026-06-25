"""라즈베리파이 Motor HAT(PCA9685 @0x6F) 제어 래퍼.

DC 모터 + 서보를 한 칩에서 제어한다. PCA9685 는 칩 전체가 '하나의 PWM 주파수'를
공유하므로 서보(60Hz)에 맞춘다(DC 도 60Hz PWM 으로 구동된다).

HAT 라이브러리는 Pi 에서만 import 되므로, 라이브러리/하드웨어가 없으면 자동으로
mock 모드로 떨어져 로그만 남긴다(PC/개발 환경에서도 노드가 죽지 않게).

사용자 검증 코드 기준:
    DC   : Raspi_MotorHAT(addr=0x6f).getMotor(2).setSpeed(s).run(FORWARD/BACKWARD/RELEASE)
    서보 : PWM(0x6F).setPWMFreq(60); setPWM(0, 0, val)   (val 200~500)
"""
from __future__ import annotations

import logging

logger = logging.getLogger("motor_hat")


class MotorHat:
    def __init__(self, addr: int = 0x6f, dc_channel: int = 2,
                 servo_channel: int = 0, freq: int = 60) -> None:
        self.addr = addr
        self.dc_channel = dc_channel
        self.servo_channel = servo_channel
        self._ok = False
        self._mh = None
        self._dc = None
        self._pwm = None
        self._consts = None
        try:
            from Raspi_MotorHAT import Raspi_MotorHAT
            self._consts = Raspi_MotorHAT
            self._mh = Raspi_MotorHAT(addr=addr)
            self._dc = self._mh.getMotor(dc_channel)
            # 서보 PWM: 같은 칩이므로 MotorHAT 내부 PWM 을 재사용(이중 init/주파수 충돌 방지).
            # 라이브러리 버전에 따라 _pwm 접근이 안 되면 별도 PWM 으로 폴백.
            try:
                self._pwm = self._mh._pwm
            except AttributeError:
                from Raspi_PWM_Servo_Driver import PWM
                self._pwm = PWM(addr)
            self._pwm.setPWMFreq(freq)
            self._ok = True
            logger.info("MotorHat OK (addr=0x%x, dc=%d, servo=%d, %dHz)",
                        addr, dc_channel, servo_channel, freq)
        except Exception as e:  # noqa: BLE001
            logger.warning("MotorHat 비활성(라이브러리/HW 없음 → mock 모드): %s", e)

    # --- DC 모터 ---
    def dc(self, direction: str, speed: int = 100) -> None:
        """direction: 'fwd' | 'back' | 'stop',  speed: 0~255."""
        speed = max(0, min(255, int(speed)))
        if not self._ok:
            logger.info("[mock] DC %s speed=%d", direction, speed)
            return
        c = self._consts
        self._dc.setSpeed(speed)
        # 모터 배선 극성이 반대로 연결돼 있어 fwd/back 이 뒤집혀 돌아간다.
        # 배선을 그대로 두고 방향 매핑을 맞바꿔(소프트웨어로 극성 흡수) 보정한다.
        if direction == "fwd":
            self._dc.run(c.BACKWARD)
        elif direction == "back":
            self._dc.run(c.FORWARD)
        else:  # stop / release
            self._dc.run(c.RELEASE)

    # --- 서보 ---
    def servo(self, val: int) -> None:
        """val: PWM off 카운트(보통 200~500). 안전을 위해 150~600 으로 클램프."""
        val = max(150, min(600, int(val)))
        if not self._ok:
            logger.info("[mock] SERVO ch=%d val=%d", self.servo_channel, val)
            return
        self._pwm.setPWM(self.servo_channel, 0, val)

    def stop(self) -> None:
        if self._ok:
            try:
                self._dc.run(self._consts.RELEASE)
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        self.stop()
