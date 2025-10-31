#!/usr/bin/env python3
"""
舵机控制进程
独立运行舵机控制逻辑，通过任务队列接收指令
"""

import multiprocessing as mp
import logging
import time
import signal
import sys
import os
import threading
from typing import Optional, Dict, Any
import serial
import fashionstar_uart_sdk as uservo

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from task_queue import TaskQueue, TaskConsumer, get_task_queue
from servo_controller import get_servo_controller

# 舵机ID常量
SERVO_ID0 = 0
SERVO_ID1 = 1

class TaskQueueMonitor:
    """任务队列监控线程"""
    
    def __init__(self, task_queue: TaskQueue, monitor_id: str = "task_monitor"):
        self.task_queue = task_queue
        self.monitor_id = monitor_id
        self.running = False
        self.thread = None
        self.logger = logging.getLogger(f"TaskMonitor-{monitor_id}")
        
    def start(self):
        """启动监控线程"""
        if self.running:
            self.logger.warning("任务队列监控已在运行")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        self.logger.info(f"任务队列监控已启动: {self.monitor_id}")
    
    def stop(self):
        """停止监控线程"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        self.logger.info(f"任务队列监控已停止: {self.monitor_id}")
    
    def _monitor_loop(self):
        """监控循环"""
        self.logger.info(f"开始监控任务队列: {self.monitor_id}")
        
        while self.running:
            try:
                # 获取任务队列状态
                queue_size = self.task_queue.qsize()
                is_empty = self.task_queue.empty()
                is_full = self.task_queue.full()
                is_executing = self.task_queue.is_executing()
                
                # 获取执行控制变量
                execution_lock_info = f"执行锁: {self.task_queue._execution_lock}"
                is_executing_value = f"执行状态值: {self.task_queue._is_executing.value}"
                
                # 获取队列中所有任务信息
                all_tasks = self.task_queue.get_all_tasks()
                
                # 打印状态信息
                self.logger.info(f"📊 任务队列状态: 大小={queue_size}, 空={is_empty}, 满={is_full}, 执行中={is_executing}")
                self.logger.info(f"🔒 执行控制变量: {execution_lock_info}, {is_executing_value}")
                
                # 打印任务详情
                if all_tasks:
                    self.logger.info("📋 当前任务队列:")
                    for i, task in enumerate(all_tasks, 1):
                        task_type = task['task_type']
                        task_data = task['data']
                        
                        if task_type == 'servo_control':
                            gesture_name = task_data.get('gesture_name', 'Unknown')
                            description = task_data.get('description', '')
                            self.logger.info(f"  {i}. 手势控制: {gesture_name} - {description}")
                        elif task_type == 'voice_servo_control':
                            angle = task_data.get('angle', 0)
                            servo_id = task_data.get('servo_id', 0)
                            client_name = task_data.get('client_name', 'unknown')
                            self.logger.info(f"  {i}. 语音控制: 舵机{servo_id} -> {angle}° (来自 {client_name})")
                        else:
                            self.logger.info(f"  {i}. {task_type}: {task_data}")
                else:
                    self.logger.info("📋 队列中无任务")
                
                self.logger.info("─" * 60)  # 分隔线
                
                # 等待1秒后再次监控
                time.sleep(0.1)
                
            except Exception as e:
                self.logger.error(f"监控任务队列时发生异常: {e}")
                time.sleep(1.0)  # 异常时也等待1秒
        
        self.logger.info(f"任务队列监控已退出: {self.monitor_id}")

class ServoProcess:
    """舵机控制进程类"""
    
    def __init__(self, process_id: str = "servo_process"):
        self.process_id = process_id
        self.task_queue = get_task_queue()
        self.consumer = TaskConsumer(self.task_queue, process_id)
        self.servo_controller = None
        self.running = False
        self.logger = None
        
        # 创建任务队列监控器
        self.task_monitor = TaskQueueMonitor(self.task_queue, f"{process_id}_monitor")
        
        # 设置信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _setup_logging(self):
        """设置日志"""
        log_format = f'%(asctime)s - {self.process_id} - %(levelname)s - %(message)s'
        logging.basicConfig(
            level=logging.INFO,
            format=log_format,
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(f'servo_process_{self.process_id}.log')
            ]
        )
        self.logger = logging.getLogger(self.process_id)
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        # if not self.running:
        #     # 如果已经在关闭过程中，直接退出
        #     self.logger.debug(f"收到信号 {signum}，进程已在关闭过程中...")
        #     sys.exit(0)
        
        # self.logger.info(f"收到信号 {signum}，开始关闭进程...")
        self.stop()
        sys.exit(0)
    
    def _initialize_servo_controller(self) -> bool:
        """初始化舵机控制器"""
        try:
            uart = serial.Serial(port='/dev/ttyUSB0', baudrate=115200,parity=serial.PARITY_NONE, stopbits=1,bytesize=8,timeout=0)
            self.servo_controller = uservo.UartServoManager(uart)
            if (self.servo_controller.ping(SERVO_ID0) == False):
                self.logger.error("舵机0未连接")
                return False
            
            self.logger.info("舵机控制器初始化成功 - 舵机0和舵机1")
            return True
            
        except Exception as e:
            self.logger.error(f"初始化舵机控制器失败: {e}")
            return False
    
    def _servo_control_callback(self, gesture_name: str, description: str) -> bool:
        """
        舵机控制回调函数（同步执行）
        
        Args:
            gesture_name: 手势名称
            description: 手势描述
            
        Returns:
            bool: 是否执行成功
        """
        try:
            if self.servo_controller is None:
                self.logger.error("舵机控制器未初始化")
                return False
            
            self.logger.info(f"执行舵机控制: {gesture_name} - {description}")
            
            # 根据手势类型执行不同的控制序列
            if gesture_name == "CLOSE_GESTURE" and "从张开到握拳" in description:
                return self._execute_close_gesture_sequence()
            elif gesture_name == "ONE_FINGER_GESTURE" and "比1手势" in description:
                return self._execute_one_finger_gesture_sequence()
            elif gesture_name == "LIKE_GESTURE" and "点赞手势" in description:
                return self._execute_like_gesture_sequence()
            elif gesture_name == "WAVE_GESTURE" and "挥手动作" in description:
                return self._execute_wave_gesture_sequence()
            else:
                self.logger.info(f"未定义的手势控制: {gesture_name} - {description}")
                return True  # 对于未定义的手势，返回成功但不执行动作
            
        except Exception as e:
            self.logger.error(f"舵机控制回调异常: {e}")
            return False
    
    def _voice_servo_control_callback(self, angle: float, servo_id: int, client_name: str) -> bool:
        """
        语音舵机控制回调函数（同步执行）
        
        Args:
            angle: 目标角度 (-180° 到 180°)
            servo_id: 舵机ID
            client_name: 客户端名称
            
        Returns:
            bool: 是否执行成功
        """
        try:
            if self.servo_controller is None:
                self.logger.error("舵机控制器未初始化")
                return False
            
            self.logger.info(f"执行语音舵机控制: 舵机{servo_id} -> 角度={angle}° (来自 {client_name})")
            
            # 验证舵机ID
            if servo_id not in [0, 1]:
                self.logger.error(f"无效的舵机ID: {servo_id}")
                return False
            
            # 验证角度范围
            angle = max(-180.0, min(180.0, float(angle)))
            
            # 检查舵机连接状态
            if not self.servo_controller.ping(servo_id):
                self.logger.error(f"舵机{servo_id}未连接")
                return False
            
            # 执行舵机角度设置
            self.servo_controller.set_servo_angle(
                servo_id=servo_id, 
                angle=angle, 
                interval=500,  # 1秒运动时间
                t_acc=250,     # 加速时间
                t_dec=250,     # 减速时间
                is_mturn=True
            )
            
            # 等待运动完成
            time.sleep(0.5)  # 稍微多等一点时间确保运动完成
            
            # 验证角度设置是否成功
            current_angle = self.servo_controller.query_servo_angle(servo_id)
            self.logger.info(f"舵机{servo_id}角度设置完成: 目标={angle}°, 当前={current_angle:.1f}°")
            
            return True
            
        except Exception as e:
            self.logger.error(f"语音舵机控制回调异常: {e}")
            return False
    
    def _execute_close_gesture_sequence(self) -> bool:
        """执行(张开🖐️->握拳✊)手势序列（使用舵机0 - 水平方向）"""
        try:
            self.logger.info("执行张开到握拳手势序列 - 舵机0（水平方向）")
            target_angle = 45.0
            
            self.servo_controller.set_servo_angle( servo_id = 0, angle = target_angle, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            self.servo_controller.set_servo_angle( servo_id = 0, angle = -target_angle, interval = 1000, t_acc=500, t_dec=500,is_mturn=True)
            time.sleep(1)
            self.servo_controller.set_servo_angle( servo_id = 0, angle = 0, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            self.logger.info("张开到握拳手势序列执行成功")
            return True
            
        except Exception as e:
            self.logger.error(f"张开到握拳手势序列执行异常: {e}")
            return False
    
    def _execute_one_finger_gesture_sequence(self) -> bool:
        """执行比1手势(握拳✊->比1☝️)序列（使用舵机1 - 垂直方向）"""
        try:
            self.logger.info("执行比1手势序列 - 舵机1（垂直方向）")
            target_angle_0 = 15.0
            target_angle_1 = 45.0
            # 抬头+右侧转头
            self.servo_controller.set_servo_angle( servo_id = 0, angle = target_angle_0, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            self.servo_controller.set_servo_angle( servo_id = 1, angle = target_angle_1, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            self.servo_controller.set_servo_angle( servo_id = 0, angle = -target_angle_0, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            self.servo_controller.set_servo_angle( servo_id = 0, angle = 0, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            self.servo_controller.set_servo_angle( servo_id = 1, angle = 60.0, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            
            self.logger.info("比1手势序列执行成功")
            return True
            
        except Exception as e:
            self.logger.error(f"比1手势序列执行异常: {e}")
            return False
    def _execute_like_gesture_sequence(self) -> bool:
        """执行点赞手势序列（使用舵机1 - 垂直方向）"""
        try:
            self.logger.info("执行点赞手势序列 - 舵机1（垂直方向）")
            target_angle = 30.0
            # 点头动作
            self.servo_controller.set_servo_angle( servo_id = 1, angle = target_angle, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            self.servo_controller.set_servo_angle( servo_id = 1, angle = 0, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            
            self.logger.info("点赞手势序列执行成功")
            return True
            
        except Exception as e:
            self.logger.error(f"点赞手势序列执行异常: {e}")
            return False
    
    def _execute_wave_gesture_sequence(self) -> bool:
        """执行挥手手势序列（使用舵机0 - 云台）"""
        try:
            self.logger.info("执行挥手手势序列 - 舵机0（云台）")
            target_angle = 45.0
            # 点头动作
            self.servo_controller.set_servo_angle( servo_id = 1, angle = target_angle, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            self.servo_controller.set_servo_angle( servo_id = 1, angle = 75.0, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            self.servo_controller.set_servo_angle( servo_id = 1, angle = target_angle, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            self.servo_controller.set_servo_angle( servo_id = 1, angle = 60.0, interval = 500, t_acc=250, t_dec=250,is_mturn=True)
            time.sleep(0.5)
            
            self.logger.info("挥手手势序列执行成功")
            return True
            
        except Exception as e:
            self.logger.error(f"挥手手势序列执行异常: {e}")
            return False
    
    def start(self):
        """启动舵机控制进程"""
        self._setup_logging()
        self.logger.info(f"启动舵机控制进程: {self.process_id}")
        
        # 初始化舵机控制器
        if not self._initialize_servo_controller():
            self.logger.error("舵机控制器初始化失败，进程退出")
            return False
        
        # 设置舵机控制回调
        self.consumer.set_servo_control_callback(self._servo_control_callback)
        
        # 设置语音舵机控制回调
        self.consumer.set_voice_servo_control_callback(self._voice_servo_control_callback)
        
        # 启动任务消费者
        self.consumer.start()
        
        # 启动任务队列监控器
        self.task_monitor.start()
        
        self.running = True
        
        self.logger.info("舵机控制进程已启动，等待任务...")
        
        try:
            # 主循环
            while self.running:
                time.sleep(0.1)
                
                # 检查舵机连接状态
                if (self.servo_controller and not self.servo_controller.ping(0)) or \
                   (self.servo_controller and not self.servo_controller.ping(1)):
                    self.logger.warning("舵机连接丢失，尝试重新连接...")
                    if not self._initialize_servo_controller():
                        self.logger.error("重新连接舵机失败")
                        time.sleep(5)  # 等待5秒后重试
                
        except KeyboardInterrupt:
            self.logger.info("收到键盘中断信号")
        except Exception as e:
            self.logger.error(f"舵机控制进程异常: {e}")
        finally:
            self.stop()
        
        return True
    
    def stop(self):
        """停止舵机控制进程"""
        if not self.running:
            return
        
        self.logger.info("正在停止舵机控制进程...")
        self.running = False
        
        # 停止任务消费者
        self.consumer.stop()
        
        # 停止任务队列监控器
        self.task_monitor.stop()
        
        # 断开舵机连接
        self._disconnect_servo_controller(self.servo_controller, "舵机0")
        
        self.logger.info("舵机控制进程已停止")
    
    def _disconnect_servo_controller(self, servo_controller, controller_name):
        """断开舵机控制器连接"""
        if servo_controller:
            try:
                # 检查是否有disconnect方法
                if hasattr(servo_controller, 'disconnect'):
                    servo_controller.disconnect()
                    self.logger.info(f"{controller_name}连接已断开")
                else:
                    # 如果没有disconnect方法，尝试关闭串口连接
                    if hasattr(servo_controller, 'uart') and servo_controller.uart:
                        servo_controller.uart.close()
                        self.logger.info(f"{controller_name}串口连接已关闭")
                    else:
                        self.logger.info(f"{controller_name}控制器已停止")
            except Exception as e:
                self.logger.error(f"断开{controller_name}连接时发生异常: {e}")

def run_servo_process(process_id: str = "servo_process"):
    """
    运行舵机控制进程的入口函数
    
    Args:
        process_id: 进程ID
    """
    servo_process = ServoProcess(process_id)
    return servo_process.start()

def create_servo_process(process_id: str = "servo_process") -> mp.Process:
    """
    创建舵机控制进程
    
    Args:
        process_id: 进程ID
        
    Returns:
        mp.Process: 舵机控制进程
    """
    process = mp.Process(
        target=run_servo_process,
        args=(process_id,),
        name=f"ServoProcess-{process_id}"
    )
    return process

class ServoProcessManager:
    """舵机进程管理器"""
    
    def __init__(self):
        self.processes: Dict[str, mp.Process] = {}
        self.logger = logging.getLogger(__name__)
    
    def start_servo_process(self, process_id: str = "servo_process") -> bool:
        """
        启动舵机控制进程
        
        Args:
            process_id: 进程ID
            
        Returns:
            bool: 是否启动成功
        """
        # 检查进程是否已存在且仍在运行
        if process_id in self.processes:
            existing_process = self.processes[process_id]
            if existing_process.is_alive():
                self.logger.warning(f"舵机进程 {process_id} 已在运行")
                return False
            else:
                # 进程已死亡，清理记录
                self.logger.info(f"清理已死亡的进程记录: {process_id}")
                del self.processes[process_id]
        
        try:
            process = create_servo_process(process_id)
            process.start()
            self.processes[process_id] = process
            
            # 等待进程启动
            time.sleep(1)
            
            if process.is_alive():
                self.logger.info(f"舵机进程 {process_id} 启动成功 (PID: {process.pid})")
                return True
            else:
                self.logger.error(f"舵机进程 {process_id} 启动失败")
                return False
                
        except Exception as e:
            self.logger.error(f"启动舵机进程失败: {e}")
            return False
    
    def stop_servo_process(self, process_id: str) -> bool:
        """
        停止舵机控制进程
        
        Args:
            process_id: 进程ID
            
        Returns:
            bool: 是否停止成功
        """
        if process_id not in self.processes:
            self.logger.warning(f"舵机进程 {process_id} 不存在")
            return False
        
        try:
            process = self.processes[process_id]
            
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)  # 等待最多10秒
                
                if process.is_alive():
                    self.logger.warning(f"舵机进程 {process_id} 未正常退出，强制终止")
                    process.kill()
                    process.join()
            
            del self.processes[process_id]
            self.logger.info(f"舵机进程 {process_id} 已停止")
            return True
            
        except Exception as e:
            self.logger.error(f"停止舵机进程失败: {e}")
            return False
    
    def stop_all_processes(self):
        """停止所有舵机进程"""
        for process_id in list(self.processes.keys()):
            self.stop_servo_process(process_id)
    
    def get_process_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有进程状态并清理死亡进程"""
        status = {}
        dead_processes = []
        
        for process_id, process in self.processes.items():
            is_alive = process.is_alive()
            status[process_id] = {
                'pid': process.pid if is_alive else None,
                'is_alive': is_alive,
                'exitcode': process.exitcode
            }
            
            # 记录已死亡的进程
            if not is_alive:
                dead_processes.append(process_id)
        
        # 清理已死亡的进程记录
        for process_id in dead_processes:
            self.logger.info(f"清理已死亡的进程记录: {process_id}")
            del self.processes[process_id]
        
        return status

# 全局进程管理器实例
_global_process_manager = None

def get_process_manager() -> ServoProcessManager:
    """获取全局进程管理器实例"""
    global _global_process_manager
    if _global_process_manager is None:
        _global_process_manager = ServoProcessManager()
    return _global_process_manager

if __name__ == "__main__":
    # 直接运行舵机控制进程
    logging.basicConfig(level=logging.INFO)
    
    print("启动舵机控制进程...")
    servo_process = ServoProcess("standalone_servo")
    
    try:
        servo_process.start()
    except KeyboardInterrupt:
        print("收到中断信号，正在退出...")
    finally:
        servo_process.stop()
        print("舵机控制进程已退出")
