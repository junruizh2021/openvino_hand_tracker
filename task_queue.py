#!/usr/bin/env python3
"""
任务队列模块
用于WebSocket服务与舵机控制进程之间的通信
"""

import multiprocessing as mp
import queue
import time
import json
import logging
from typing import Dict, Any, Optional, Union
from dataclasses import dataclass, asdict
from enum import Enum
import threading

class TaskType(Enum):
    """任务类型枚举"""
    SERVO_CONTROL = "servo_control"
    VOICE_SERVO_CONTROL = "voice_servo_control"
    GESTURE_DETECTED = "gesture_detected"
    SYSTEM_STATUS = "system_status"
    SHUTDOWN = "shutdown"

@dataclass
class Task:
    """任务数据结构"""
    task_id: str
    task_type: TaskType
    timestamp: float
    data: Dict[str, Any]
    priority: int = 0  # 优先级，数字越小优先级越高
    retry_count: int = 0
    max_retries: int = 3
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'task_id': self.task_id,
            'task_type': self.task_type.value,
            'timestamp': self.timestamp,
            'data': self.data,
            'priority': self.priority,
            'retry_count': self.retry_count,
            'max_retries': self.max_retries
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Task':
        """从字典创建任务"""
        return cls(
            task_id=data['task_id'],
            task_type=TaskType(data['task_type']),
            timestamp=data['timestamp'],
            data=data['data'],
            priority=data.get('priority', 0),
            retry_count=data.get('retry_count', 0),
            max_retries=data.get('max_retries', 3)
        )

class TaskQueue:
    """任务队列管理器"""
    
    def __init__(self, maxsize: int = 1):
        """
        初始化任务队列
        
        Args:
            maxsize: 队列最大大小
        """
        self.maxsize = maxsize
        self._queue = mp.Queue(maxsize=maxsize)
        self._task_counter = mp.Value('i', 0)
        self._lock = mp.Lock()
        self._execution_lock = mp.Lock()  # 执行锁，防止执行期间接收新任务
        self._is_executing = mp.Value('b', False)  # 是否正在执行任务
        self._logger = logging.getLogger(__name__)
        
    def put(self, task: Task, block: bool = True, timeout: Optional[float] = None) -> bool:
        """
        添加任务到队列
        
        Args:
            task: 要添加的任务
            block: 是否阻塞等待
            timeout: 超时时间
            
        Returns:
            bool: 是否成功添加
        """
        try:
            self._queue.put(task.to_dict(), block=block, timeout=timeout)
            self._logger.debug(f"任务已添加到队列: {task.task_id}")
            return True
        except queue.Full:
            self._logger.warning(f"队列已满，无法添加任务: {task.task_id}")
            return False
        except Exception as e:
            self._logger.error(f"添加任务失败: {e}")
            return False
    
    def get(self, block: bool = True, timeout: Optional[float] = None) -> Optional[Task]:
        """
        从队列获取任务
        
        Args:
            block: 是否阻塞等待
            timeout: 超时时间
            
        Returns:
            Task: 获取到的任务，如果队列为空则返回None
        """
        try:
            task_dict = self._queue.get(block=block, timeout=timeout)
            task = Task.from_dict(task_dict)
            self._logger.debug(f"从队列获取任务: {task.task_id}")
            return task
        except queue.Empty:
            return None
        except Exception as e:
            self._logger.error(f"获取任务失败: {e}")
            return None
    
    def qsize(self) -> int:
        """获取队列大小"""
        return self._queue.qsize()
    
    def get_gesture_names(self) -> list:
        """获取队列中所有舵机控制任务的gesture_name列表"""
        gesture_names = []
        temp_tasks = []
        
        try:
            # 取出所有任务
            while not self._queue.empty():
                task_dict = self._queue.get(block=False)
                task = Task.from_dict(task_dict)
                temp_tasks.append(task)
                
                # 只收集SERVO_CONTROL类型的任务的gesture_name
                if task.task_type == TaskType.SERVO_CONTROL:
                    gesture_name = task.data.get('gesture_name', 'Unknown')
                    gesture_names.append(gesture_name)
            
            # 将任务放回队列
            for task in temp_tasks:
                self._queue.put(task.to_dict(), block=False)
                
        except Exception as e:
            # 只在调试模式下记录错误，避免日志噪音
            self._logger.debug(f"获取gesture_name列表失败: {e}")
        
        return gesture_names
    
    def get_all_tasks(self) -> list:
        """获取队列中所有任务的信息列表"""
        all_tasks = []
        temp_tasks = []
        
        try:
            # 取出所有任务
            while not self._queue.empty():
                task_dict = self._queue.get(block=False)
                task = Task.from_dict(task_dict)
                temp_tasks.append(task)
                
                # 收集所有任务的基本信息
                task_info = {
                    'task_id': task.task_id,
                    'task_type': task.task_type.value,
                    'timestamp': task.timestamp,
                    'data': task.data
                }
                all_tasks.append(task_info)
            
            # 将任务放回队列
            for task in temp_tasks:
                self._queue.put(task.to_dict(), block=False)
                
        except Exception as e:
            # 只在调试模式下记录错误，避免日志噪音
            self._logger.debug(f"获取任务列表失败: {e}")
        
        return all_tasks
    
    def empty(self) -> bool:
        """检查队列是否为空"""
        return self._queue.empty()
    
    def full(self) -> bool:
        """检查队列是否已满"""
        return self._queue.full()
    
    def generate_task_id(self) -> str:
        """生成唯一任务ID"""
        with self._lock:
            self._task_counter.value += 1
            return f"task_{int(time.time() * 1000)}_{self._task_counter.value}"
    
    def start_execution(self) -> bool:
        """
        开始执行任务，设置执行状态
        
        Returns:
            bool: 是否成功开始执行
        """
        with self._execution_lock:
            if self._is_executing.value:
                self._logger.warning("已有任务正在执行，无法开始新任务")
                return False
            self._is_executing.value = True
            self._logger.debug("开始执行任务")
            return True
    
    def finish_execution(self):
        """完成任务执行，清除执行状态"""
        with self._execution_lock:
            self._is_executing.value = False
            self._logger.debug("任务执行完成")
    
    def is_executing(self) -> bool:
        """检查是否正在执行任务"""
        with self._execution_lock:
            return self._is_executing.value

class TaskProducer:
    """任务生产者（WebSocket服务端使用）"""
    
    def __init__(self, task_queue: TaskQueue):
        self.task_queue = task_queue
        self._logger = logging.getLogger(__name__)
    
    def create_servo_control_task(self, gesture_name: str, description: str, 
                                 priority: int = 0) -> bool:
        """
        创建舵机控制任务
        
        Args:
            gesture_name: 手势名称
            description: 手势描述
            priority: 任务优先级
            
        Returns:
            bool: 是否成功创建任务
        """
        # 检查是否正在执行舵机任务
        if self.task_queue.is_executing():
            self._logger.warning(f"正在执行舵机任务，拒绝新的舵机控制任务: {gesture_name}")
            return False
        
        task_id = self.task_queue.generate_task_id()
        task = Task(
            task_id=task_id,
            task_type=TaskType.SERVO_CONTROL,
            timestamp=time.time(),
            data={
                'gesture_name': gesture_name,
                'description': description,
                'action': 'execute_gesture'
            },
            priority=priority
        )
        
        success = self.task_queue.put(task)
        if success:
            self._logger.info(f"舵机控制任务已创建: {gesture_name} ({task_id})")
        else:
            self._logger.error(f"创建舵机控制任务失败: {gesture_name}")
        # 只在调试模式下打印队列信息
        self._print_queue_gesture_names()
        return success
    
    def create_voice_servo_control_task(self, angle: float, client_name: str = 'unknown',
                                       priority: int = 0) -> bool:
        """
        创建语音舵机控制任务
        
        Args:
            angle: 目标角度 (-180° 到 180°)
            client_name: 客户端名称
            priority: 任务优先级
            
        Returns:
            bool: 是否成功创建任务
        """
        # 检查是否正在执行舵机任务
        if self.task_queue.is_executing():
            self._logger.warning(f"正在执行舵机任务，拒绝新的语音舵机控制任务: 角度={angle}°")
            return False
        
        # 验证角度范围
        angle = max(-180.0, min(180.0, float(angle)))
        
        task_id = self.task_queue.generate_task_id()
        task = Task(
            task_id=task_id,
            task_type=TaskType.VOICE_SERVO_CONTROL,
            timestamp=time.time(),
            data={
                'angle': angle,
                'client_name': client_name,
                'action': 'set_servo_angle',
                'servo_id': 0  # 默认控制舵机0
            },
            priority=priority
        )
        
        success = self.task_queue.put(task)
        if success:
            self._logger.info(f"语音舵机控制任务已创建: 角度={angle}° (来自 {client_name}) ({task_id})")
        else:
            self._logger.error(f"创建语音舵机控制任务失败: 角度={angle}°")
        
        return success
    
    def _print_queue_gesture_names(self):
        """打印任务队列中所有任务的gesture_name"""
        gesture_names = self.task_queue.get_gesture_names()
        if gesture_names:
            self._logger.debug(f"任务队列中的gesture_name: {gesture_names}")
        # 移除空队列的日志，减少日志噪音
    
    def is_executing(self) -> bool:
        """检查是否正在执行任务"""
        return self.task_queue.is_executing()
    
    def create_gesture_detected_task(self, gesture_data: Dict[str, Any], 
                                   priority: int = 1) -> bool:
        """
        创建手势检测任务
        
        Args:
            gesture_data: 手势数据
            priority: 任务优先级
            
        Returns:
            bool: 是否成功创建任务
        """
        task_id = self.task_queue.generate_task_id()
        task = Task(
            task_id=task_id,
            task_type=TaskType.GESTURE_DETECTED,
            timestamp=time.time(),
            data=gesture_data,
            priority=priority
        )
        
        success = self.task_queue.put(task)
        if success:
            self._logger.debug(f"手势检测任务已创建: {task_id}")
        else:
            self._logger.error(f"创建手势检测任务失败: {task_id}")
        
        return success
    
    def create_system_status_task(self, status_data: Dict[str, Any], 
                                priority: int = 2) -> bool:
        """
        创建系统状态任务
        
        Args:
            status_data: 状态数据
            priority: 任务优先级
            
        Returns:
            bool: 是否成功创建任务
        """
        task_id = self.task_queue.generate_task_id()
        task = Task(
            task_id=task_id,
            task_type=TaskType.SYSTEM_STATUS,
            timestamp=time.time(),
            data=status_data,
            priority=priority
        )
        
        success = self.task_queue.put(task)
        if success:
            self._logger.debug(f"系统状态任务已创建: {task_id}")
        else:
            self._logger.error(f"创建系统状态任务失败: {task_id}")
        
        return success
    
    def create_shutdown_task(self, priority: int = 0) -> bool:
        """
        创建关闭任务
        
        Args:
            priority: 任务优先级
            
        Returns:
            bool: 是否成功创建任务
        """
        task_id = self.task_queue.generate_task_id()
        task = Task(
            task_id=task_id,
            task_type=TaskType.SHUTDOWN,
            timestamp=time.time(),
            data={'reason': 'system_shutdown'},
            priority=priority
        )
        
        success = self.task_queue.put(task)
        if success:
            self._logger.info(f"系统关闭任务已创建: {task_id}")
        else:
            self._logger.error(f"创建系统关闭任务失败: {task_id}")
        
        return success

class TaskConsumer:
    """任务消费者（舵机控制进程使用）"""
    
    def __init__(self, task_queue: TaskQueue, consumer_id: str = "default"):
        self.task_queue = task_queue
        self.consumer_id = consumer_id
        self._logger = logging.getLogger(f"{__name__}.{consumer_id}")
        self._running = False
        self._thread = None
    
    def start(self):
        """启动任务消费者"""
        if self._running:
            self._logger.warning("任务消费者已在运行")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._consume_tasks, daemon=True)
        self._thread.start()
        self._logger.info(f"任务消费者已启动: {self.consumer_id}")
    
    def stop(self):
        """停止任务消费者"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        self._logger.info(f"任务消费者已停止: {self.consumer_id}")
    
    def _consume_tasks(self):
        """消费任务的主循环"""
        self._logger.info(f"开始消费任务: {self.consumer_id}")
        
        while self._running:
            try:
                # 获取任务，设置超时避免无限阻塞
                task = self.task_queue.get(block=True, timeout=1.0)
                if task is None:
                    continue
                
                # 处理任务
                self._process_task(task)
                
            except Exception as e:
                self._logger.error(f"处理任务时发生异常: {e}")
                time.sleep(0.1)  # 短暂休眠避免CPU占用过高
        
        self._logger.info(f"任务消费者已退出: {self.consumer_id}")
    
    def _process_task(self, task: Task):
        """
        处理单个任务
        
        Args:
            task: 要处理的任务
        """
        is_servo_task = task.task_type in [TaskType.SERVO_CONTROL, TaskType.VOICE_SERVO_CONTROL]
        
        try:
            self._logger.debug(f"处理任务: {task.task_id} ({task.task_type.value})")
            
            # 立即设置执行标志，防止生产者在处理期间添加新任务
            if is_servo_task:
                if not self.task_queue.start_execution():
                    self._logger.warning(f"无法开始执行任务，可能已有任务在执行: {task.task_id}")
                    return
            
            if task.task_type == TaskType.SERVO_CONTROL:
                self._handle_servo_control_task(task)
            elif task.task_type == TaskType.VOICE_SERVO_CONTROL:
                self._handle_voice_servo_control_task(task)
            elif task.task_type == TaskType.GESTURE_DETECTED:
                self._handle_gesture_detected_task(task)
            elif task.task_type == TaskType.SYSTEM_STATUS:
                self._handle_system_status_task(task)
            elif task.task_type == TaskType.SHUTDOWN:
                self._handle_shutdown_task(task)
            else:
                self._logger.warning(f"未知任务类型: {task.task_type}")
                
        except Exception as e:
            self._logger.error(f"处理任务失败 {task.task_id}: {e}")
        finally:
            # 无论成功还是失败，都要清除执行状态（仅针对舵机任务）
            if is_servo_task:
                self.task_queue.finish_execution()
    
    def _handle_servo_control_task(self, task: Task):
        """处理舵机控制任务"""
        gesture_name = task.data.get('gesture_name')
        description = task.data.get('description')
        
        # 注意：执行标志已经在 _process_task 中设置，也由其负责清除
        self._logger.info(f"执行舵机控制: {gesture_name} - {description}")
        
        # 这里应该调用实际的舵机控制逻辑
        # 为了解耦，我们使用回调函数
        if hasattr(self, 'servo_control_callback'):
            try:
                success = self.servo_control_callback(gesture_name, description)
                if success:
                    self._logger.debug(f"舵机控制执行成功: {gesture_name}")
                else:
                    self._logger.warning(f"舵机控制执行失败: {gesture_name}")
            except Exception as e:
                self._logger.error(f"舵机控制回调异常: {e}")
                raise  # 重新抛出异常，让上层处理
        else:
            self._logger.warning("未设置舵机控制回调函数")
    
    def _handle_voice_servo_control_task(self, task: Task):
        """处理语音舵机控制任务"""
        angle = task.data.get('angle')
        client_name = task.data.get('client_name', 'unknown')
        servo_id = task.data.get('servo_id', 0)
        
        # 注意：执行标志已经在 _process_task 中设置，也由其负责清除
        self._logger.info(f"执行语音舵机控制: 舵机{servo_id} -> 角度={angle}° (来自 {client_name})")
        
        # 调用语音舵机控制回调函数
        if hasattr(self, 'voice_servo_control_callback'):
            try:
                success = self.voice_servo_control_callback(angle, servo_id, client_name)
                if success:
                    self._logger.info(f"语音舵机控制执行成功: 舵机{servo_id} -> 角度={angle}°")
                else:
                    self._logger.warning(f"语音舵机控制执行失败: 舵机{servo_id} -> 角度={angle}°")
            except Exception as e:
                self._logger.error(f"语音舵机控制回调异常: {e}")
                raise  # 重新抛出异常，让上层处理
        else:
            self._logger.warning("未设置语音舵机控制回调函数")
    
    def _handle_gesture_detected_task(self, task: Task):
        """处理手势检测任务"""
        self._logger.debug(f"处理手势检测任务: {task.data}")
        # 可以在这里添加手势检测相关的处理逻辑
    
    def _handle_system_status_task(self, task: Task):
        """处理系统状态任务"""
        self._logger.debug(f"处理系统状态任务: {task.data}")
        # 可以在这里添加系统状态相关的处理逻辑
    
    def _handle_shutdown_task(self, task: Task):
        """处理关闭任务"""
        self._logger.info(f"收到关闭信号: {task.data.get('reason', 'unknown')}")
        self._running = False
    
    def set_servo_control_callback(self, callback):
        """设置舵机控制回调函数"""
        self.servo_control_callback = callback
    
    def set_voice_servo_control_callback(self, callback):
        """设置语音舵机控制回调函数"""
        self.voice_servo_control_callback = callback

# 全局任务队列实例
_global_task_queue = None
_global_task_producer = None

def get_task_queue() -> TaskQueue:
    """获取全局任务队列实例"""
    global _global_task_queue
    if _global_task_queue is None:
        _global_task_queue = TaskQueue()
    return _global_task_queue

def get_task_producer() -> TaskProducer:
    """获取全局任务生产者实例"""
    global _global_task_producer
    if _global_task_producer is None:
        _global_task_producer = TaskProducer(get_task_queue())
    return _global_task_producer

if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.INFO)
    
    # 创建任务队列和生产者
    task_queue = TaskQueue()
    producer = TaskProducer(task_queue)
    
    # 创建消费者
    consumer = TaskConsumer(task_queue, "test_consumer")
    
    # 设置舵机控制回调
    def test_servo_callback(gesture_name, description):
        print(f"执行舵机控制: {gesture_name} - {description}")
        time.sleep(2)  # 模拟舵机控制耗时，增加时间以便观察效果
        return True
    
    # 设置语音舵机控制回调
    def test_voice_servo_callback(angle, servo_id, client_name):
        print(f"执行语音舵机控制: 舵机{servo_id} -> 角度={angle}° (来自 {client_name})")
        time.sleep(1)  # 模拟舵机控制耗时
        return True
    
    consumer.set_servo_control_callback(test_servo_callback)
    consumer.set_voice_servo_control_callback(test_voice_servo_callback)
    
    # 启动消费者
    consumer.start()
    
    try:
        # 创建一些测试任务
        print("=== 测试任务队列执行控制 ===")
        
        # 第一个任务
        print("1. 创建第一个任务...")
        success1 = producer.create_servo_control_task("FIVE", "张开手掌")
        print(f"   任务创建结果: {success1}")
        
        # 立即尝试创建第二个任务（应该被拒绝）
        print("2. 立即尝试创建第二个任务...")
        success2 = producer.create_servo_control_task("FIST", "握拳")
        print(f"   任务创建结果: {success2} (应该被拒绝)")
        
        # 等待第一个任务执行完成
        print("3. 等待第一个任务执行完成...")
        time.sleep(3)
        
        # 检查执行状态
        print(f"4. 当前执行状态: {producer.is_executing()}")
        
        # 现在应该可以创建新任务
        print("5. 创建第二个任务...")
        success3 = producer.create_servo_control_task("FIST", "握拳")
        print(f"   任务创建结果: {success3}")
        
        # 创建其他类型的任务（应该不受影响）
        print("6. 创建手势检测任务...")
        producer.create_gesture_detected_task({"gesture": "WAVE", "confidence": 0.9})
        
        # 等待任务处理完成
        time.sleep(3)
        
        # 测试语音舵机控制任务
        print("7. 创建语音舵机控制任务...")
        success4 = producer.create_voice_servo_control_task(angle=45.0, client_name="test_client")
        print(f"   语音舵机控制任务创建结果: {success4}")
        
        # 等待语音舵机控制任务执行完成
        time.sleep(2)
        
        # 测试另一个语音舵机控制任务
        print("8. 创建第二个语音舵机控制任务...")
        success5 = producer.create_voice_servo_control_task(angle=-30.0, client_name="test_client2")
        print(f"   第二个语音舵机控制任务创建结果: {success5}")
        
        # 等待任务处理完成
        time.sleep(2)
        
        # 发送关闭信号
        producer.create_shutdown_task()
        
        # 等待消费者停止
        consumer.stop()
        
    except KeyboardInterrupt:
        print("测试被中断")
        consumer.stop()
