/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  *
  * AI 자동 조준 시스템 - STM32 Nucleo-F401RE 펌웨어
  *  - USART1(PA9=TX, PA10=RX, 115200 8N1) 로 Raspberry Pi 와 통신
  *    (USART1 은 ST-LINK VCP 와 무관하여 외부 UART 통신에 안전. PA9=D8, PA10=D2)
  *  - TIM3 PWM 으로 서보 3개 제어: CH1=Pan, CH2=Tilt, CH3=Trigger(발사 걸쇠)
  *
  * Pi -> STM32 명령 (5바이트):
  *   [0xAA][pan(0-180)][tilt(0-180)][trigger(0/1)][checksum]
  *   checksum = (pan + tilt + trigger) & 0xFF
  * STM32 -> Pi 상태 (4바이트):
  *   [0x55][pan_current][tilt_current][checksum]
  *   checksum = (pan_current + tilt_current) & 0xFF
  *
  * ※ CubeMX 에서 USART1 global interrupt(NVIC) 를 반드시 Enable 해야 한다
  *    (stm32f4xx_it.c 의 USART1_IRQHandler 가 HAL_UART_IRQHandler(&huart1) 호출).
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define CMD_HEADER        0xAA   /* Pi -> STM32 명령 헤더 */
#define STATUS_HEADER     0x55   /* STM32 -> Pi 상태 헤더 */
#define CMD_LEN           5      /* 명령 패킷 총 길이 */

#define SERVO_PAN         1      /* TIM3_CH1 */
#define SERVO_TILT        2      /* TIM3_CH2 */
#define SERVO_TRIGGER     3      /* TIM3_CH3 */

#define TRIGGER_REST_DEG   120   /* 평상시(걸쇠 유지) 각도 — 시작 자세 */
#define TRIGGER_FIRE_DEG   0     /* 발사(걸쇠 해제) 각도. REST 보다 작게 = 시계방향 회전 발사 */
#define TRIGGER_HOLD_MS    500   /* 발사 후 걸쇠 위치 유지 시간(이후 자동 복귀) */

#define STATUS_PERIOD_MS   50    /* 상태 송신 주기 */
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */
/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
TIM_HandleTypeDef htim3;

UART_HandleTypeDef huart1;

/* USER CODE BEGIN PV */
static uint8_t  rx_byte;                 /* UART 1바이트 수신 버퍼 */
static uint8_t  cmd_buf[CMD_LEN];        /* 명령 패킷 조립 버퍼 */
static uint8_t  cmd_idx = 0;             /* 조립 인덱스 */

static volatile uint8_t  pan_target  = 90;   /* 목표 각도(명령 수신) */
static volatile uint8_t  tilt_target = 0;    /* tilt 0° = 수평(대포 가동 0~45°) */
static volatile uint8_t  trigger_cmd = 0;    /* 발사 명령 플래그 */
static volatile uint8_t  new_cmd = 0;        /* 새 명령 도착 플래그 */

static uint8_t  pan_current  = 90;       /* 현재 적용 각도 */
static uint8_t  tilt_current = 0;        /* tilt 0° = 수평(대포 가동 0~45°) */
static uint8_t  trigger_active = 0;      /* 발사 걸쇠 해제 상태 */
static uint32_t trigger_start_ms = 0;    /* 발사 시각(자동 복귀용) */
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_USART1_UART_Init(void);
static void MX_TIM3_Init(void);
/* USER CODE BEGIN PFP */
static void send_status(void);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
void _SERVO_CONTROL(int num, int angle){
   if(angle < 0)   angle = 0;
   if(angle > 180) angle = 180;
   int target_angle = 500 + (angle * 2000) / 180;   /* 0.5ms~2.5ms @ 50Hz */
   if(num == SERVO_PAN){
      __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_1, target_angle);
   }
   else if(num == SERVO_TILT){
      __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_2, target_angle);
   }
   else if(num == SERVO_TRIGGER){
      __HAL_TIM_SET_COMPARE(&htim3, TIM_CHANNEL_3, target_angle);
   }
}

/* STM32 -> Pi 상태 패킷 전송 (4바이트) */
static void send_status(void){
   uint8_t tx[4];
   tx[0] = STATUS_HEADER;
   tx[1] = pan_current;
   tx[2] = tilt_current;
   tx[3] = (uint8_t)(pan_current + tilt_current);
   HAL_UART_Transmit(&huart1, tx, sizeof(tx), 10);
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{
  /* USER CODE BEGIN 1 */
  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/
  HAL_Init();

  /* USER CODE BEGIN Init */
  /* USER CODE END Init */

  SystemClock_Config();

  /* USER CODE BEGIN SysInit */
  /* USER CODE END SysInit */

  MX_GPIO_Init();
  MX_USART1_UART_Init();
  MX_TIM3_Init();
  /* USER CODE BEGIN 2 */
  HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_1);
  HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_2);
  HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_3);

  /* 초기 자세: pan/tilt 중립(90), trigger 걸쇠 유지 */
  _SERVO_CONTROL(SERVO_PAN, pan_current);
  _SERVO_CONTROL(SERVO_TILT, tilt_current);
  _SERVO_CONTROL(SERVO_TRIGGER, TRIGGER_REST_DEG);

  /* USART1 수신 인터럽트 시작 (1바이트씩) */
  HAL_NVIC_SetPriority(USART1_IRQn, 5, 0);
  HAL_NVIC_EnableIRQ(USART1_IRQn);
  HAL_UART_Receive_IT(&huart1, &rx_byte, 1);

  uint32_t last_status_ms = HAL_GetTick();
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
     /* 새 명령 적용 */
     if(new_cmd){
        new_cmd = 0;
        pan_current  = pan_target;
        tilt_current = tilt_target;
        _SERVO_CONTROL(SERVO_PAN, pan_current);
        _SERVO_CONTROL(SERVO_TILT, tilt_current);

        if(trigger_cmd && !trigger_active){
           /* 발사: 걸쇠 해제 */
           _SERVO_CONTROL(SERVO_TRIGGER, TRIGGER_FIRE_DEG);
           trigger_active = 1;
           trigger_start_ms = HAL_GetTick();
        }
     }

     /* 발사 후 일정 시간 뒤 걸쇠 위치 자동 복귀(재장전 가능하도록) */
     if(trigger_active && (HAL_GetTick() - trigger_start_ms >= TRIGGER_HOLD_MS)){
        _SERVO_CONTROL(SERVO_TRIGGER, TRIGGER_REST_DEG);
        trigger_active = 0;
     }

     /* 주기적 상태 송신 */
     if(HAL_GetTick() - last_status_ms >= STATUS_PERIOD_MS){
        last_status_ms = HAL_GetTick();
        send_status();
     }
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE2);

  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 16;
  RCC_OscInitStruct.PLL.PLLN = 336;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV4;
  RCC_OscInitStruct.PLL.PLLQ = 7;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief TIM3 Initialization Function
  */
static void MX_TIM3_Init(void)
{
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};

  htim3.Instance = TIM3;
  htim3.Init.Prescaler = 84-1;            /* 84MHz/84 = 1MHz -> 1us tick */
  htim3.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim3.Init.Period = 20000-1;            /* 20ms -> 50Hz */
  htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_PWM_Init(&htim3) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim3, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 0;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  if (HAL_TIM_PWM_ConfigChannel(&htim3, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim3, &sConfigOC, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim3, &sConfigOC, TIM_CHANNEL_3) != HAL_OK)
  {
    Error_Handler();
  }
  HAL_TIM_MspPostInit(&htim3);
}

/**
  * @brief USART1 Initialization Function
  */
static void MX_USART1_UART_Init(void)
{
  huart1.Instance = USART1;
  huart1.Init.BaudRate = 115200;
  huart1.Init.WordLength = UART_WORDLENGTH_8B;
  huart1.Init.StopBits = UART_STOPBITS_1;
  huart1.Init.Parity = UART_PARITY_NONE;
  huart1.Init.Mode = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief GPIO Initialization Function
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_RESET);

  GPIO_InitStruct.Pin = B1_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(B1_GPIO_Port, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = LD2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(LD2_GPIO_Port, &GPIO_InitStruct);
}

/* USER CODE BEGIN 4 */
/**
  * @brief UART 수신 완료 콜백 - Pi 명령 패킷 파싱 상태머신
  *   [0xAA][pan][tilt][trigger][checksum]
  */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
   if(huart->Instance == USART1){
      uint8_t b = rx_byte;

      if(cmd_idx == 0){
         /* 헤더 동기화 */
         if(b == CMD_HEADER){
            cmd_buf[cmd_idx++] = b;
         }
      } else {
         cmd_buf[cmd_idx++] = b;
         if(cmd_idx >= CMD_LEN){
            /* 패킷 완성: 체크섬 검증 */
            uint8_t pan  = cmd_buf[1];
            uint8_t tilt = cmd_buf[2];
            uint8_t trig = cmd_buf[3];
            uint8_t chk  = cmd_buf[4];
            if(chk == (uint8_t)(pan + tilt + trig)){
               pan_target  = pan;
               tilt_target = tilt;
               trigger_cmd = trig;
               new_cmd = 1;
            }
            cmd_idx = 0;   /* 다음 패킷 대기 */
         }
      }

      /* 다음 1바이트 수신 재무장 */
      HAL_UART_Receive_IT(&huart1, &rx_byte, 1);
   }
}
/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
