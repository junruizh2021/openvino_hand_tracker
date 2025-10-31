'''
总线伺服舵机
> Python SDK角度设置 Example <
注意：运行前确认零点位置是否和图片一致
--------------------------------------------------
 * 作者: 深圳市华馨京科技有限公司
 * 网站：https://fashionrobo.com/
 * 更新时间: 2025/9/25
--------------------------------------------------
'''

import time
import serial
import logging
import fashionstar_uart_sdk as uservo

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('servo_control.log')
    ]
)
logger = logging.getLogger(__name__)


SERVO_PORT_NAME =  '/dev/ttyUSB0' 	# 端口号
SERVO_BAUDRATE = 115200 			# 波特率默认115200
SERVO_ID0 = 0  						# 舵机ID0
SERVO_ID1 = 1 						# 舵机ID1


if __name__ == '__main__':
	uart = serial.Serial(port=SERVO_PORT_NAME, baudrate=SERVO_BAUDRATE,parity=serial.PARITY_NONE, stopbits=1,bytesize=8,timeout=0)
	control = uservo.UartServoManager(uart)
	print("ping-开始")
	
	
	while 1:
		ping_ok_flag = 0
		#查询舵机是否在线
		if (control.ping(SERVO_ID0) == True and control.ping(SERVO_ID1) == True):
			break
		else:
			time.sleep(0.02)
	print("ping-成功")

	print("清理圈数-开始")
	while 1:
		init_flag = 1
		#查询当前角度，如果角度超出范围，则清理圈数
		servo0_current_angle = control.query_servo_angle(servo_id = SERVO_ID0)
		servo1_current_angle = control.query_servo_angle(servo_id = SERVO_ID1)
		if  servo0_current_angle >180.0 or servo0_current_angle <-180.0:
			init_flag = 0
		if servo1_current_angle >180.0 or servo1_current_angle <-180.0:
			init_flag = 0

		if init_flag:
			break
		else:
			#清理圈数
			control.reset_multi_turn_angle(0xff)
			time.sleep(0.02)

	print("清理圈数-完成，开始运行")
	#control.set_servo_angle( servo_id = SERVO_ID0, angle = 0, interval = 1000, t_acc=500, t_dec=500,is_mturn=True)
	# servo_id：舵机ID
	# angle：角度
	# interval：总执行时间 ms
	# t_acc：加速时间 ms
	# t_dec：减速时间 ms
	# is_mturn：是否多圈（默认True)
	control.set_servo_angle( servo_id = SERVO_ID0, angle = 0.0, interval = 1000, t_acc=500, t_dec=500,is_mturn=True)
	logger.info(f"舵机{SERVO_ID0}执行完成: 角度=0°, 执行时间=1000ms")
	time.sleep(1)
	control.set_servo_angle( servo_id = SERVO_ID1, angle = 60.0, interval = 1000, t_acc=500, t_dec=500,is_mturn=True)
	logger.info(f"舵机{SERVO_ID1}执行完成: 角度=60°, 执行时间=1000ms")
	time.sleep(1)
	print("舵机初始化完成")
