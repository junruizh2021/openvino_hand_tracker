#!/usr/bin/env python3
"""
主服务器程序
协调WebSocket服务和舵机控制进程
"""

import asyncio
import logging
import signal
import sys
import time
import argparse
from typing import Optional
from openvino import Core

# 添加项目根目录到Python路径
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

available_devices = Core().available_devices
device = "NPU" if 'NPU' in available_devices else 'CPU'
print("available_devices = {}, selected device = {}".format(available_devices, device))

from servo_process import get_process_manager
from task_queue import get_task_queue, get_task_producer

class MainServer:
    """主服务器类"""
    
    def __init__(self):
        self.process_manager = get_process_manager()
        self.task_queue = get_task_queue()
        self.task_producer = get_task_producer()
        self.websocket_server = None
        self.running = False
        self.logger = None
        
        # 设置信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _setup_logging(self, log_level: str = "INFO"):
        """设置日志"""
        level = getattr(logging, log_level.upper(), logging.INFO)
        log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        
        logging.basicConfig(
            level=level,
            format=log_format,
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler('main_server.log')
            ]
        )
        self.logger = logging.getLogger('MainServer')
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        # if not self.running:
        #     # 如果已经在关闭过程中，直接退出
        #     self.logger.info(f"收到信号 {signum}，服务器已在关闭过程中...")
        #     return
        
        # self.logger.info(f"收到信号 {signum}，开始关闭服务器...")
        # 设置停止标志，让主循环处理关闭
        self.running = False
    
    async def _start_websocket_server(self, host: str = "0.0.0.0", port: int = 8765, 
                                    dynamic_gestures: bool = True, **kwargs):
        """启动WebSocket服务器"""
        try:
            # 导入WebSocket服务器模块
            import ws_handtracker_server
            import websockets
            
            # 验证模块是否正确加载
            if not hasattr(ws_handtracker_server, 'handtracker_websocket_handler'):
                raise ImportError("WebSocket处理器函数未找到")
            
            self.logger.info("WebSocket模块导入成功")
            
            self.logger.info(f"启动WebSocket服务器: {host}:{port}")
            
            # 设置全局参数，模拟命令行参数
            import argparse
            args = argparse.Namespace()
            
            # 基本参数
            args.input = kwargs.get('input', '0')
            args.gesture = kwargs.get('gesture', True)  # -g 参数
            args.pd_m = kwargs.get('pd_m', '/home/gesture-controller/AI-models/palm_detection_FP32.xml')
            args.pd_device = kwargs.get('pd_device', device)
            args.no_lm = kwargs.get('no_lm', False)
            args.lm_m = kwargs.get('lm_m', '/home/gesture-controller/AI-models/hand_landmark_FP32.xml')
            args.lm_device = kwargs.get('lm_device', device)
            args.crop = kwargs.get('crop', False)
            args.no_gui = True  # WebSocket模式下强制无头模式
            args.right_hand_only = kwargs.get('right_hand_only', False)  # 默认只处理右手
            
            # 显示控制参数 - 设置为默认True
            args.show_pd_box = kwargs.get('show_pd_box', False)
            args.show_pd_kps = kwargs.get('show_pd_kps', False)
            args.show_rot_rect = kwargs.get('show_rot_rect', False)
            args.show_landmarks = kwargs.get('show_landmarks', True)  # 默认显示手部关键点
            args.show_handedness = kwargs.get('show_handedness', False)
            args.show_scores = kwargs.get('show_scores', False)  # 默认显示分数
            args.show_gesture_display = kwargs.get('show_gesture_display', False)
            args.show_original_video = kwargs.get('show_original_video', False)
            
            # 关闭显示的参数
            args.hide_pd_box = kwargs.get('hide_pd_box', False)
            args.hide_pd_kps = kwargs.get('hide_pd_kps', False)
            args.hide_rot_rect = kwargs.get('hide_rot_rect', False)
            args.hide_landmarks = kwargs.get('hide_landmarks', False)
            args.hide_handedness = kwargs.get('hide_handedness', False)
            args.hide_scores = kwargs.get('hide_scores', False)
            args.hide_gesture_display = kwargs.get('hide_gesture_display', False)
            args.hide_original_video = kwargs.get('hide_original_video', False)
            
            # 动态手势处理参数
            args.dynamic_gestures = kwargs.get('dynamic_gestures', True)  # 默认启用动态手势
            args.gesture_window_size = kwargs.get('gesture_window_size', 8)
            args.gesture_confidence = kwargs.get('gesture_confidence', 0.6)
            args.enable_wave_detection = kwargs.get('enable_wave_detection', False)
            args.wave_threshold = kwargs.get('wave_threshold', 0.2)
            args.wave_min_movement = kwargs.get('wave_min_movement', 50)
            args.wave_window_size = kwargs.get('wave_window_size', 5)
            
            # 舵机控制参数
            args.servo_port = kwargs.get('servo_port', '/dev/ttyUSB0')
            args.servo_baudrate = kwargs.get('servo_baudrate', 115200)
            args.servo_id = kwargs.get('servo_id', 0)
            args.enable_servo = kwargs.get('enable_servo', False)  # 由主服务器控制舵机进程
            
            # 将args设置为全局变量，供ws-handtracker-server.py使用
            ws_handtracker_server.args = args
            
            # 初始化HandTracker
            try:
                await ws_handtracker_server.initialize_handtracker()
                self.logger.info("HandTracker初始化成功")
            except Exception as e:
                self.logger.error(f"HandTracker初始化失败: {e}")
                raise
            
            # 启动WebSocket服务器
            try:
                self.websocket_server = await websockets.serve(
                    ws_handtracker_server.handtracker_websocket_handler, 
                    host, port
                )
                self.logger.info("WebSocket服务器创建成功")
            except Exception as e:
                self.logger.error(f"WebSocket服务器创建失败: {e}")
                raise
            
            self.logger.info(f"WebSocket服务器已启动: ws://{host}:{port}")
            self.logger.info("参数配置:")
            self.logger.info(f"  - 手势识别: {args.gesture}")
            self.logger.info(f"  - 动态手势: {args.dynamic_gestures}")
            self.logger.info(f"  - 显示关键点: {args.show_landmarks}")
            self.logger.info(f"  - 显示分数: {args.show_scores}")
            self.logger.info(f"  - 只处理右手: {args.right_hand_only}")
            
        except Exception as e:
            self.logger.error(f"启动WebSocket服务器失败: {e}")
            raise
    
    def _start_servo_process(self, process_id: str = "servo_process") -> bool:
        """启动舵机控制进程"""
        try:
            success = self.process_manager.start_servo_process(process_id)
            if success:
                self.logger.info(f"舵机控制进程 {process_id} 启动成功")
            else:
                self.logger.error(f"舵机控制进程 {process_id} 启动失败")
            return success
        except Exception as e:
            self.logger.error(f"启动舵机控制进程异常: {e}")
            return False
    
    async def start(self, host: str = "0.0.0.0", port: int = 8765, 
                   dynamic_gestures: bool = True, enable_servo: bool = True,
                   servo_process_id: str = "servo_process", 
                   gesture: bool = True,  # -g 参数
                   show_landmarks: bool = True,  # 显示手部关键点
                   show_scores: bool = True,  # 显示分数
                   right_hand_only: bool = True,  # 只处理右手
                   **kwargs):
        """
        启动主服务器
        
        Args:
            host: WebSocket服务器主机
            port: WebSocket服务器端口
            dynamic_gestures: 是否启用动态手势识别
            enable_servo: 是否启用舵机控制
            servo_process_id: 舵机进程ID
            **kwargs: 其他参数
        """
        self.logger.info("启动主服务器...")
        
        # 启动舵机控制进程（如果启用）
        if enable_servo:
            if not self._start_servo_process(servo_process_id):
                self.logger.warning("舵机控制进程启动失败，继续运行但不支持舵机控制")
        else:
            self.logger.info("舵机控制已禁用")
        
        # 启动WebSocket服务器
        try:
            await self._start_websocket_server(
                host=host,
                port=port,
                dynamic_gestures=dynamic_gestures,
                gesture=gesture,
                show_landmarks=show_landmarks,
                show_scores=show_scores,
                right_hand_only=right_hand_only,
                **kwargs
            )
        except Exception as e:
            self.logger.error(f"启动WebSocket服务器失败: {e}")
            await self.shutdown()
            return False
        
        self.running = True
        self.logger.info("主服务器启动完成")
        
        # 主循环
        try:
            while self.running:
                await asyncio.sleep(0.5)  # 减少睡眠时间，提高响应速度
                
                # 检查舵机进程状态
                if enable_servo:
                    status = self.process_manager.get_process_status()
                    for pid, proc_status in status.items():
                        if not proc_status['is_alive']:
                            self.logger.warning(f"舵机进程 {pid} 已停止，尝试重启...")
                            success = self._start_servo_process(pid)
                            if success:
                                self.logger.info(f"舵机进程 {pid} 重启成功")
                            else:
                                self.logger.error(f"舵机进程 {pid} 重启失败")
                
        except KeyboardInterrupt:
            self.logger.info("收到键盘中断信号")
        except Exception as e:
            self.logger.error(f"主服务器运行异常: {e}")
        finally:
            await self.shutdown()
    
    async def shutdown(self):
        """关闭主服务器"""
        if not self.running:
            return
        
        self.logger.info("正在关闭主服务器...")
        self.running = False
        
        # 停止WebSocket服务器
        if self.websocket_server:
            try:
                await self.websocket_server.stop()
                self.logger.info("WebSocket服务器已停止")
            except Exception as e:
                self.logger.error(f"停止WebSocket服务器异常: {e}")
        
        # 停止所有舵机进程
        try:
            self.process_manager.stop_all_processes()
            self.logger.info("所有舵机进程已停止")
        except Exception as e:
            self.logger.error(f"停止舵机进程异常: {e}")
        
        # 发送关闭任务到队列
        try:
            self.task_producer.create_shutdown_task()
            self.logger.info("关闭任务已发送")
        except Exception as e:
            self.logger.error(f"发送关闭任务异常: {e}")
        
        self.logger.info("主服务器已关闭")

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="手势识别WebSocket服务器")
    
    # WebSocket服务器参数
    parser.add_argument("--host", default="0.0.0.0", help="WebSocket服务器主机")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket服务器端口")
    
    # 功能开关
    parser.add_argument("--dynamic_gestures", action="store_true", default=True,
                       help="启用动态手势识别")
    parser.add_argument("--enable_servo", action="store_true", default=True,
                       help="启用舵机控制")
    parser.add_argument("--no-servo", action="store_true", 
                       help="禁用舵机控制")
    
    # 舵机控制参数
    parser.add_argument("--servo_process_id", default="servo_process", 
                       help="舵机进程ID")
    
    # 日志参数
    parser.add_argument("--log_level", default="INFO", 
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       help="日志级别")
    
    # 其他参数
    parser.add_argument("--right_hand_only", action="store_true",
                       help="只处理右手")
    parser.add_argument("--enable_wave_detection", action="store_true",
                       help="启用挥手检测")
    
    return parser.parse_args()

async def main():
    """主函数"""
    args = parse_arguments()
    
    # 创建主服务器实例
    server = MainServer()
    server._setup_logging(args.log_level)
    
    # 确定是否启用舵机控制
    enable_servo = args.enable_servo and not args.no_servo
    
    # 启动服务器
    await server.start(
        host=args.host,
        port=args.port,
        dynamic_gestures=args.dynamic_gestures,
        enable_servo=enable_servo,
        servo_process_id=args.servo_process_id,
        gesture=True,  # 默认启用手势识别
        show_landmarks=True,  # 默认显示手部关键点
        show_scores=False,  # 默认显示分数
        right_hand_only=False,  # 默认只处理右手
        enable_wave_detection=True
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")
    except Exception as e:
        print(f"程序异常退出: {e}")
        sys.exit(1)
