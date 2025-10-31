import logging
from sys import thread_info
import numpy as np
from collections import namedtuple
import mediapipe_utils as mpu
import cv2
from pathlib import Path
from FPS import FPS, now
import argparse
import os
from openvino import Core
import asyncio
import websockets
import json
import base64
from collections import deque
from datetime import datetime


# 角度映射工具：将环形阵列麦克风角度(顺时针为 360->180->0)映射为舵机角度(顺时针 0->180,-180->0)
def map_mic_angle_to_servo(angle_deg: float) -> float:
    """
    将麦克风角度(0..360，顺时针递减: 360->180->0)映射为舵机角度([-180,180]，顺时针: 0->180,-180->0)。
    规则:
    1) 规范化麦克风角度到 [0, 360)
    2) 计算顺时针角度 φ_cw = (360 - mic) % 360 （因为麦克风顺时针数值递减）
    3) 舵机角度 s = wrap_to_180(φ_cw) = ((φ_cw + 180) % 360) - 180
    """
    try:
        mic = float(angle_deg)
    except (ValueError, TypeError):
        return 0.0
    mic_norm = mic % 360.0
    phi_cw = (360.0 - mic_norm) % 360.0
    servo = ((phi_cw + 180.0) % 360.0) - 180.0
    return servo

# 设置Qt环境变量以解决Docker容器中的Qt平台插件问题
os.environ['QT_QPA_PLATFORM'] = 'xcb'
os.environ['QT_X11_NO_MITSHM'] = '1'
os.environ['QT_DEBUG_PLUGINS'] = '0'


class DynamicGestureProcessor:
    """动态手势处理器（集成挥手检测）"""
    
    def __init__(self, window_size=10, min_confidence=0.7, enable_wave_detection=False, 
                 wave_threshold=0.2, wave_min_movement=10, wave_window_size=10):
        """
        初始化动态手势处理器
        
        Args:
            window_size: 滑动窗口大小（帧数）
            min_confidence: 最小置信度阈值
            enable_wave_detection: 是否启用挥手检测
            wave_threshold: 挥手检测阈值比例
            wave_min_movement: 最小移动像素数
            wave_window_size: 挥手检测滑动窗口大小
        """
        self.window_size = window_size
        self.min_confidence = min_confidence
        self.gesture_history = deque(maxlen=window_size)  # 存储手势历史
        self.last_gesture = None
        self.gesture_count = 0
        self.enable_wave_detection = enable_wave_detection
        
        # 挥手检测相关属性
        self.wave_threshold = wave_threshold
        self.wave_min_movement = wave_min_movement
        self.wave_window_size = wave_window_size
        self.landmark_history = deque(maxlen=wave_window_size)  # 存储关键点历史
        self.frame_width = 640  # 默认宽度
        
        # 定义动态手势模式
        self.dynamic_patterns = {
            # "CLOSE_GESTURE": {
            #     "pattern": ["FIVE", "FIST"],
            #     "description": "从张开到握拳"
            # },
            "ONE_FINGER_GESTURE": {
                "pattern": ["FIST", "ONE"],
                "description": "比1手势"
            },
            "TWO_FINGER_GESTURE": {
                "pattern": ["FIST", "TWO"],
                "description": "比2手势"
            },
            # "LIKE_GESTURE": {
            #     "pattern": ["FIST", "OK"],
            #     "description": "点赞手势"
            # },
            # 滑动窗口挥手模式
            "WAVE_GESTURE": {
                "pattern": ["FIVE","WAVE", "FIVE"],
                "description": "挥手动作"
            },
        }
    
    def detect_wave_with_sliding_window(self, landmarks, frame_width):
        """
        使用滑动窗口检测挥手动作
        
        Args:
            landmarks: 当前帧的手部关键点
            frame_width: 图像宽度
            
        Returns:
            str: "WAVE" 或 None
        """
        if not self.enable_wave_detection or landmarks is None or len(landmarks) < 21:
            return None
        
        # 更新帧宽度
        self.frame_width = frame_width
        threshold = self.wave_threshold * frame_width
        
        # 获取关键点索引（对应handwave-demo.py中的关键点）
        # 8: 食指指尖, 12: 中指指尖, 16: 无名指指尖, 20: 小指指尖
        key_points = [8, 12, 16, 20]
        
        # 提取当前帧的关键点
        current_points = []
        for idx in key_points:
            if idx < len(landmarks):
                # landmarks格式: [x, y, z] 其中x,y是归一化坐标
                x = landmarks[idx][0] * frame_width  # 转换为像素坐标
                y = landmarks[idx][1] * frame_width
                current_points.append((x, y))
            else:
                return None
        
        # 添加到滑动窗口
        self.landmark_history.append({
            'points': current_points,
            'timestamp': datetime.now()
        })
        
        # 需要至少2帧数据才能检测挥手
        if len(self.landmark_history) < 2:
            return None
        
        # 使用滑动窗口检测挥手
        return self._analyze_wave_pattern()
    
    def _analyze_wave_pattern(self):
        """
        分析滑动窗口中的挥手模式
        
        Returns:
            str: "WAVE" 或 None
        """
        if len(self.landmark_history) < 2:
            return None
        
        # 获取滑动窗口中的所有关键点
        all_points = [frame['points'] for frame in self.landmark_history]
        
        # 计算每个关键点的移动轨迹
        movements = []
        for point_idx in range(len(all_points[0])):  # 遍历每个关键点
            point_movements = []
            for frame_idx in range(1, len(all_points)):  # 从第二帧开始
                curr_point = all_points[frame_idx][point_idx]
                prev_point = all_points[frame_idx - 1][point_idx]
                
                dx = curr_point[0] - prev_point[0]
                dy = curr_point[1] - prev_point[1]
                point_movements.append((dx, dy))
            
            movements.append(point_movements)
        
        # 分析移动模式
        wave_detected = self._detect_wave_direction(movements)
        
        if wave_detected:
            # 清空历史记录，避免重复触发
            self.landmark_history.clear()
            return "WAVE"
        
        return None
    
    def _detect_wave_direction(self, movements):
        """
        检测挥手方向
        
        Args:
            movements: 关键点移动轨迹列表
            
        Returns:
            bool: 是否检测到挥手
        """
        if not movements or not movements[0]:
            return False
        
        # 计算每个关键点的平均移动方向
        avg_movements = []
        for point_movements in movements:
            if not point_movements:
                return False
            
            # 计算平均移动
            avg_dx = sum(mov[0] for mov in point_movements) / len(point_movements)
            avg_dy = sum(mov[1] for mov in point_movements) / len(point_movements)
            avg_movements.append((avg_dx, avg_dy))
        
        # 检查是否所有关键点都向同一方向移动
        ## threshold = self.wave_threshold * self.frame_width
        threshold = 10
        
        # 检查向右挥手
        if all(mov[0] > threshold for mov in avg_movements):
            logging.info("向右挥手")
            return True
        
        # # 检查向左挥手
        if all(mov[0] < -threshold for mov in avg_movements):
            logging.info("向左挥手")
            return True
        
        # # # 检查向上挥手
        # if all(mov[1] < -threshold for mov in avg_movements):
        #     logging.info("向上挥手")
        #     return True
        
        # # # 检查向下挥手
        # if all(mov[1] > threshold for mov in avg_movements):
        #     logging.info("向下挥手")
        #     return True
        
        return False
    
    async def process_frame(self, hand_info, frame_width=640):
        """
        处理单帧手势信息
        
        Args:
            hand_info: 包含手势信息的字典
            frame_width: 图像宽度，用于挥手检测
        """
        if not hand_info:
            return
        
        current_gesture = hand_info.get('gesture')
        confidence = hand_info.get('score', 0)
        landmarks = hand_info.get('landmarks', [])
        
        # 使用滑动窗口检测挥手动作
        wave_gesture = None
        if self.enable_wave_detection and landmarks:
            wave_gesture = self.detect_wave_with_sliding_window(landmarks, frame_width)
        
        # 确定最终手势（优先使用挥手检测结果）
        final_gesture = wave_gesture if wave_gesture else current_gesture
        
        # 只处理置信度足够高的静态手势，挥手检测不受置信度限制
        if not wave_gesture and confidence < self.min_confidence:
            return
        
        # 添加到历史记录
        self.gesture_history.append({
            'gesture': final_gesture,
            'confidence': confidence,
            'timestamp': datetime.now(),
            'is_wave': wave_gesture is not None
        })
        
        # 检查动态手势模式
        await self._check_dynamic_patterns()
    
    async def _check_dynamic_patterns(self):
        """检查动态手势模式"""
        if len(self.gesture_history) < 2:
            return
        
        # 获取最近的手势序列
        recent_gestures = [frame['gesture'] for frame in self.gesture_history]
        
        # 检查每个预定义模式
        for pattern_name, pattern_info in self.dynamic_patterns.items():
            if self._matches_pattern(recent_gestures, pattern_info['pattern']):
                # 清空历史记录，避免重复触发
                self.gesture_history.clear()
                self._trigger_dynamic_gesture(pattern_name, pattern_info['description'])
                break
    
    def _matches_pattern(self, gesture_sequence, pattern):
        """
        检查手势序列是否匹配模式
        
        Args:
            gesture_sequence: 实际手势序列
            pattern: 要匹配的模式
            
        Returns:
            bool: 是否匹配
        """
        # 打印手势序列和模式（已关闭）
        # print(f"手势序列: {gesture_sequence}")
        # print(f"模式: {pattern}")
        
        if not pattern:
            return True
        if not gesture_sequence:
            return False
        
        # 子序列匹配算法
        # 支持模式中的手势在序列中按顺序出现但允许中间有间隔
        pattern_index = 0
        pattern_len = len(pattern)
        
        for gesture in gesture_sequence:
            if gesture == pattern[pattern_index]:
                pattern_index += 1
                if pattern_index == pattern_len:
                    return True
        
        return False
    
    def _trigger_dynamic_gesture(self, pattern_name, description):
        """触发动态手势事件"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] 动态手势检测: {pattern_name} - {description}")
        
        # 使用任务队列发送舵机控制指令（非阻塞）
        try:
            from task_queue import get_task_producer
            producer = get_task_producer()
            
            # 创建舵机控制任务
            success = producer.create_servo_control_task(
                gesture_name=pattern_name,
                description=description,
                priority=0  # 高优先级
            )
            
            if success:
                print(f"[{timestamp}] 舵机控制任务已发送到队列: {pattern_name}")
            else:
                print(f"[{timestamp}] 发送舵机控制任务失败: {pattern_name}")
                
        except ImportError:
            print(f"[{timestamp}] 任务队列模块未找到，跳过舵机控制")
        except Exception as e:
            print(f"[{timestamp}] 舵机控制任务发送异常: {e}")
    
    def get_gesture_statistics(self):
        """获取手势统计信息"""
        if not self.gesture_history:
            return {}
        
        gesture_counts = {}
        for frame in self.gesture_history:
            gesture = frame['gesture']
            gesture_counts[gesture] = gesture_counts.get(gesture, 0) + 1
        
        return {
            'total_frames': len(self.gesture_history),
            'gesture_distribution': gesture_counts,
            'window_size': self.window_size
        }

# 全局动态手势处理器实例（将在main函数中初始化）
dynamic_processor = None

class HandTracker:
    def __init__(self, input_src=None,
                pd_xml="models/palm_detection_FP32.xml", 
                pd_device="NPU",
                pd_score_thresh=0.5, pd_nms_thresh=0.3,
                use_lm=True,
                lm_xml="models/hand_landmark_FP32.xml",
                lm_device="NPU",
                lm_score_threshold=0.5,
                use_gesture=False,
                crop=False,
                no_gui=False,
                right_hand_only=False,
                show_pd_box=None, show_pd_kps=None, show_rot_rect=None,
                show_landmarks=None, show_handedness=None, show_scores=None,
                show_gesture_display=None, show_original_video=None):
        
        self.pd_score_thresh = pd_score_thresh
        self.pd_nms_thresh = pd_nms_thresh
        self.use_lm = use_lm
        self.lm_score_threshold = lm_score_threshold
        self.use_gesture = use_gesture
        self.crop = crop
        self.no_gui = no_gui
        self.right_hand_only = right_hand_only
        
        # 跟踪摄像头索引
        self.current_camera_index = None
        
        # 显示原始视频窗口的标志
        self.show_original_video = True
        
        if input_src.endswith('.jpg') or input_src.endswith('.png') :
            self.image_mode = True
            self.img = cv2.imread(input_src)
        else:
            self.image_mode = False
            if input_src.isdigit():
                input_src = int(input_src)
                self.current_camera_index = input_src
            else:
                self.current_camera_index = 0  # 默认摄像头
            self.cap = cv2.VideoCapture(input_src)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    
        # Create SSD anchors 
        # https://github.com/google/mediapipe/blob/master/mediapipe/modules/palm_detection/palm_detection_cpu.pbtxt
        anchor_options = mpu.SSDAnchorOptions(num_layers=4, 
                                min_scale=0.1484375,
                                max_scale=0.75,
                                input_size_height=128,
                                input_size_width=128,
                                anchor_offset_x=0.5,
                                anchor_offset_y=0.5,
                                strides=[8, 16, 16, 16],
                                aspect_ratios= [1.0],
                                reduce_boxes_in_lowest_layer=False,
                                interpolated_scale_aspect_ratio=1.0,
                                fixed_anchor_size=True)
        self.anchors = mpu.generate_anchors(anchor_options)
        self.nb_anchors = self.anchors.shape[0]
        print(f"{self.nb_anchors} anchors have been created")

        # Load Openvino models
        self.load_models(pd_xml, pd_device, lm_xml, lm_device)

        # Rendering flags - 使用传入参数或默认值
        if self.use_lm:
            self.show_pd_box = show_pd_box if show_pd_box is not None else False
            self.show_pd_kps = show_pd_kps if show_pd_kps is not None else False
            self.show_rot_rect = show_rot_rect if show_rot_rect is not None else False
            self.show_handedness = show_handedness if show_handedness is not None else False
            self.show_landmarks = show_landmarks if show_landmarks is not None else True
            self.show_scores = show_scores if show_scores is not None else False
            self.show_gesture = show_gesture_display if show_gesture_display is not None else self.use_gesture
        else:
            self.show_pd_box = show_pd_box if show_pd_box is not None else True
            self.show_pd_kps = show_pd_kps if show_pd_kps is not None else False
            self.show_rot_rect = show_rot_rect if show_rot_rect is not None else False
            self.show_scores = show_scores if show_scores is not None else False
        
        # 原始视频窗口显示控制
        self.show_original_video = show_original_video if show_original_video is not None else True
        

    def load_models(self, pd_xml, pd_device, lm_xml, lm_device):

        print("Loading OpenVINO Runtime")
        self.core = Core()
        print("Device info:")
        print(f"{' '*8}{pd_device}")
        try:
            print(f"{' '*8}OpenVINO Runtime version: {self.core.get_property('RUNTIME_VERSION', pd_device)}")
        except:
            print(f"{' '*8}OpenVINO Runtime loaded successfully")

        # Palm detection model
        print("Palm Detection model - Reading model file:\n\t{}".format(pd_xml))
        self.pd_model = self.core.read_model(pd_xml)
        # Input tensor: input - shape: [1, 3, 128, 128]
        # Output tensor: classificators - shape: [1, 896, 1] : scores
        # Output tensor: regressors - shape: [1, 896, 18] : bboxes
        self.pd_input_tensor = self.pd_model.input(0)
        print(f"Input tensor: {list(self.pd_input_tensor.names)[0] if self.pd_input_tensor.names else 'unnamed'} - shape: {self.pd_input_tensor.shape}")
        _,_,self.pd_h,self.pd_w = self.pd_input_tensor.shape
        for output in self.pd_model.outputs:
            output_name = list(output.names)[0] if output.names else 'unnamed'
            print(f"Output tensor: {output_name} - shape: {output.shape}")
            if "classificators" in output_name:
                self.pd_scores = output_name
            elif "regressors" in output_name:
                self.pd_bboxes = output_name
        print("Loading palm detection model into the device")
        self.pd_compiled_model = self.core.compile_model(self.pd_model, pd_device)
        self.pd_infer_time_cumul = 0
        self.pd_infer_nb = 0

        self.infer_nb = 0
        self.infer_time_cumul = 0

        # Landmarks model
        if self.use_lm:
            if lm_device != pd_device:
                print("Device info:")
                print(f"{' '*8}{lm_device}")
                try:
                    print(f"{' '*8}OpenVINO Runtime version: {self.core.get_property('RUNTIME_VERSION', lm_device)}")
                except:
                    print(f"{' '*8}OpenVINO Runtime loaded successfully")

            print("Landmark model - Reading model file:\n\t{}".format(lm_xml))
            self.lm_model = self.core.read_model(lm_xml)
            # Input tensor: input_1 - shape: [1, 3, 224, 224]
            # Output tensor: Identity_1 - shape: [1, 1]
            # Output tensor: Identity_2 - shape: [1, 1]
            # Output tensor: Identity_dense/BiasAdd/Add - shape: [1, 63]
            self.lm_input_tensor = self.lm_model.input(0)
            print(f"Input tensor: {list(self.lm_input_tensor.names)[0] if self.lm_input_tensor.names else 'unnamed'} - shape: {self.lm_input_tensor.shape}")
            _,_,self.lm_h,self.lm_w = self.lm_input_tensor.shape
            # Batch reshaping if lm_2 is True
            for output in self.lm_model.outputs:
                output_name = list(output.names)[0] if output.names else 'unnamed'
                print(f"Output tensor: {output_name} - shape: {output.shape}")
                if "Identity_1" in output_name:
                    self.lm_score = output_name
                elif "Identity_2" in output_name:
                    self.lm_handedness = output_name
                elif "Identity_dense" in output_name:
                    self.lm_landmarks = output_name
            print("Loading landmark model to the device")
            self.lm_compiled_model = self.core.compile_model(self.lm_model, lm_device)
            self.lm_infer_time_cumul = 0
            self.lm_infer_nb = 0
            self.lm_hand_nb = 0

    
    def pd_postprocess(self, inference):
        scores = np.squeeze(inference[self.pd_scores])  # 896
        bboxes = inference[self.pd_bboxes][0] # 896x18
        # Decode bboxes
        self.regions = mpu.decode_bboxes(self.pd_score_thresh, scores, bboxes, self.anchors)
        # Non maximum suppression
        self.regions = mpu.non_max_suppression(self.regions, self.pd_nms_thresh)
        if self.use_lm:
            mpu.detections_to_rect(self.regions)
            mpu.rect_transformation(self.regions, self.frame_size, self.frame_size)

    def pd_render(self, frame):
        for r in self.regions:
            if self.show_pd_box:
                box = (np.array(r.pd_box) * self.frame_size).astype(int)
                cv2.rectangle(frame, (box[0], box[1]), (box[0]+box[2], box[1]+box[3]), (0,255,0), 2)
            if self.show_pd_kps:
                for i,kp in enumerate(r.pd_kps):
                    x = int(kp[0] * self.frame_size)
                    y = int(kp[1] * self.frame_size)
                    cv2.circle(frame, (x, y), 6, (0,0,255), -1)
                    cv2.putText(frame, str(i), (x, y+12), cv2.FONT_HERSHEY_PLAIN, 1.5, (0,255,0), 2)
            if self.show_scores:
                cv2.putText(frame, f"Palm score: {r.pd_score:.2f}", 
                        (int(r.pd_box[0] * self.frame_size+10), int((r.pd_box[1]+r.pd_box[3])*self.frame_size+60)), 
                        cv2.FONT_HERSHEY_PLAIN, 2, (255,255,0), 2)

    def recognize_gesture(self, r):           

        # Finger states
        # state: -1=unknown, 0=close, 1=open
        d_3_5 = mpu.distance(r.landmarks[3], r.landmarks[5])
        d_2_3 = mpu.distance(r.landmarks[2], r.landmarks[3])
        angle0 = mpu.angle(r.landmarks[0], r.landmarks[1], r.landmarks[2])
        angle1 = mpu.angle(r.landmarks[1], r.landmarks[2], r.landmarks[3])
        angle2 = mpu.angle(r.landmarks[2], r.landmarks[3], r.landmarks[4])
        r.thumb_angle = angle0+angle1+angle2
        if angle0+angle1+angle2 > 460 and d_3_5 / d_2_3 > 1.2: 
            r.thumb_state = 1
        else:
            r.thumb_state = 0

        if r.landmarks[8][1] < r.landmarks[7][1] < r.landmarks[6][1]:
            r.index_state = 1
        elif r.landmarks[6][1] < r.landmarks[8][1]:
            r.index_state = 0
        else:
            r.index_state = -1

        if r.landmarks[12][1] < r.landmarks[11][1] < r.landmarks[10][1]:
            r.middle_state = 1
        elif r.landmarks[10][1] < r.landmarks[12][1]:
            r.middle_state = 0
        else:
            r.middle_state = -1

        if r.landmarks[16][1] < r.landmarks[15][1] < r.landmarks[14][1]:
            r.ring_state = 1
        elif r.landmarks[14][1] < r.landmarks[16][1]:
            r.ring_state = 0
        else:
            r.ring_state = -1

        if r.landmarks[20][1] < r.landmarks[19][1] < r.landmarks[18][1]:
            r.little_state = 1
        elif r.landmarks[18][1] < r.landmarks[20][1]:
            r.little_state = 0
        else:
            r.little_state = -1

        # Gesture
        if r.thumb_state == 1 and r.index_state == 1 and r.middle_state == 1 and r.ring_state == 1 and r.little_state == 1:
            r.gesture = "FIVE"
        elif r.thumb_state == 0 and r.index_state == 0 and r.middle_state == 0 and r.ring_state == 0 and r.little_state == 0:
            r.gesture = "FIST"
        elif r.thumb_state == 1 and r.index_state == 0 and r.middle_state == 0 and r.ring_state == 0 and r.little_state == 0:
            r.gesture = "OK" 
        elif r.thumb_state == 0 and r.index_state == 1 and r.middle_state == 1 and r.ring_state == 0 and r.little_state == 0:
            r.gesture = "PEACE"
        elif r.thumb_state == 0 and r.index_state == 1 and r.middle_state == 0 and r.ring_state == 0 and r.little_state == 0:
            r.gesture = "ONE"
        elif r.thumb_state == 1 and r.index_state == 1 and r.middle_state == 0 and r.ring_state == 0 and r.little_state == 0:
            r.gesture = "TWO"
        elif r.thumb_state == 1 and r.index_state == 1 and r.middle_state == 1 and r.ring_state == 0 and r.little_state == 0:
            r.gesture = "THREE"
        elif r.thumb_state == 0 and r.index_state == 1 and r.middle_state == 1 and r.ring_state == 1 and r.little_state == 1:
            r.gesture = "FOUR"
        else:
            r.gesture = None
            
    def lm_postprocess(self, region, inference):
        region.lm_score = np.squeeze(inference[self.lm_score])    
        region.handedness = np.squeeze(inference[self.lm_handedness])
        lm_raw = np.squeeze(inference[self.lm_landmarks])
        
        lm = []
        for i in range(int(len(lm_raw)/3)):
            # x,y,z -> x/w,y/h,z/w (here h=w)
            lm.append(lm_raw[3*i:3*(i+1)]/self.lm_w)
        region.landmarks = lm
        if self.use_gesture: self.recognize_gesture(region)


    
    def lm_render(self, frame, region):
        if region.lm_score > self.lm_score_threshold:
            if self.show_rot_rect:
                cv2.polylines(frame, [np.array(region.rect_points)], True, (0,255,255), 2, cv2.LINE_AA)
            if self.show_landmarks:
                src = np.array([(0, 0), (1, 0), (1, 1)], dtype=np.float32)
                dst = np.array([ (x, y) for x,y in region.rect_points[1:]], dtype=np.float32) # region.rect_points[0] is left bottom point !
                mat = cv2.getAffineTransform(src, dst)
                lm_xy = np.expand_dims(np.array([(l[0], l[1]) for l in region.landmarks]), axis=0)
                lm_xy = np.squeeze(cv2.transform(lm_xy, mat)).astype(np.int32)
                list_connections = [[0, 1, 2, 3, 4], 
                                    [0, 5, 6, 7, 8], 
                                    [5, 9, 10, 11, 12],
                                    [9, 13, 14 , 15, 16],
                                    [13, 17],
                                    [0, 17, 18, 19, 20]]
                lines = [np.array([lm_xy[point] for point in line]) for line in list_connections]
                cv2.polylines(frame, lines, False, (255, 0, 0), 2, cv2.LINE_AA)
                if self.use_gesture:
                    # color depending on finger state (1=open, 0=close, -1=unknown)
                    color = { 1: (0,255,0), 0: (0,0,255), -1:(0,255,255)}
                    radius = 6
                    cv2.circle(frame, (lm_xy[0][0], lm_xy[0][1]), radius, color[-1], -1)
                    for i in range(1,5):
                        cv2.circle(frame, (lm_xy[i][0], lm_xy[i][1]), radius, color[region.thumb_state], -1)
                    for i in range(5,9):
                        cv2.circle(frame, (lm_xy[i][0], lm_xy[i][1]), radius, color[region.index_state], -1)
                    for i in range(9,13):
                        cv2.circle(frame, (lm_xy[i][0], lm_xy[i][1]), radius, color[region.middle_state], -1)
                    for i in range(13,17):
                        cv2.circle(frame, (lm_xy[i][0], lm_xy[i][1]), radius, color[region.ring_state], -1)
                    for i in range(17,21):
                        cv2.circle(frame, (lm_xy[i][0], lm_xy[i][1]), radius, color[region.little_state], -1)
                else:
                    for x,y in lm_xy:
                        cv2.circle(frame, (x, y), 6, (0,128,255), -1)
            if self.show_handedness:
                cv2.putText(frame, f"RIGHT {region.handedness:.2f}" if region.handedness > 0.5 else f"LEFT {1-region.handedness:.2f}", 
                        (int(region.pd_box[0] * self.frame_size+10), int((region.pd_box[1]+region.pd_box[3])*self.frame_size+20)), 
                        cv2.FONT_HERSHEY_PLAIN, 2, (0,255,0) if region.handedness > 0.5 else (0,0,255), 2)
            if self.show_scores:
                cv2.putText(frame, f"Landmark score: {region.lm_score:.2f}", 
                        (int(region.pd_box[0] * self.frame_size+10), int((region.pd_box[1]+region.pd_box[3])*self.frame_size+90)), 
                        cv2.FONT_HERSHEY_PLAIN, 2, (255,255,0), 2)
            if self.use_gesture and self.show_gesture:
                cv2.putText(frame, region.gesture, (int(region.pd_box[0]*self.frame_size+10), int(region.pd_box[1]*self.frame_size-50)), 
                        cv2.FONT_HERSHEY_PLAIN, 3, (255,255,255), 3)

        

    def run(self):
        """同步版本的运行方法，用于直接运行"""
        self.fps = FPS(mean_nb_frames=20)

        nb_pd_inferences = 0
        nb_lm_inferences = 0
        glob_pd_rtrip_time = 0
        glob_lm_rtrip_time = 0
        while True:
            self.fps.update()
            if self.image_mode:
                vid_frame = self.img
            else:
                ok, vid_frame = self.cap.read()
                if not ok:
                    break
            h, w = vid_frame.shape[:2]
            if self.crop:
                # Cropping the long side to get a square shape
                self.frame_size = min(h, w)
                dx = (w - self.frame_size) // 2
                dy = (h - self.frame_size) // 2
                video_frame = vid_frame[dy:dy+self.frame_size, dx:dx+self.frame_size]
            else:
                # Padding on the small side to get a square shape
                self.frame_size = max(h, w)
                pad_h = int((self.frame_size - h)/2)
                pad_w = int((self.frame_size - w)/2)
                video_frame = cv2.copyMakeBorder(vid_frame, pad_h, pad_h, pad_w, pad_w, cv2.BORDER_CONSTANT)

            # Resize image to NN square input shape
            frame_nn = cv2.resize(video_frame, (self.pd_w, self.pd_h), interpolation=cv2.INTER_AREA)
            # Transpose hxwx3 -> 1x3xhxw
            frame_nn = np.transpose(frame_nn, (2,0,1))[None,]

            annotated_frame = video_frame.copy()

            # Get palm detection
            pd_rtrip_time = now()
            input_name = list(self.pd_input_tensor.names)[0] if self.pd_input_tensor.names else 'input'
            inference = self.pd_compiled_model({input_name: frame_nn})
            glob_pd_rtrip_time += now() - pd_rtrip_time
            self.pd_postprocess(inference)
            self.pd_render(annotated_frame)
            nb_pd_inferences += 1

            # Hand landmarks
            if self.use_lm:
                for i,r in enumerate(self.regions):
                    frame_nn = mpu.warp_rect_img(r.rect_points, video_frame, self.lm_w, self.lm_h)
                    # Transpose hxwx3 -> 1x3xhxw
                    frame_nn = np.transpose(frame_nn, (2,0,1))[None,]
                    # Get hand landmarks
                    lm_rtrip_time = now()
                    lm_input_name = list(self.lm_input_tensor.names)[0] if self.lm_input_tensor.names else 'input_1'
                    inference = self.lm_compiled_model({lm_input_name: frame_nn})
                    glob_lm_rtrip_time += now() - lm_rtrip_time
                    nb_lm_inferences += 1
                    self.lm_postprocess(r, inference)
                    
                    # 检查是否只处理右手
                    if self.right_hand_only:
                        handedness = float(getattr(r, 'handedness', 0))
                        if handedness <= 0.5:  # 只处理右手
                            continue
                    
                    self.lm_render(annotated_frame, r)

            if not self.crop:
                annotated_frame = annotated_frame[pad_h:pad_h+h, pad_w:pad_w+w]

            self.fps.display(annotated_frame, orig=(50,50),color=(240,180,100))
            
            # 检查是否在无头环境中运行
            if not self.no_gui:
                try:
                    # 显示处理后的视频（带检测结果）
                    cv2.imshow("Hand Tracking Result", annotated_frame)
                    
                    # 显示原始视频输入（如果启用）
                    if self.show_original_video:
                        original_frame = vid_frame.copy()
                        
                        # 在原始视频上添加详细信息
                        y_offset = 30
                        cv2.putText(original_frame, f"Original Input - Camera: {self.current_camera_index if self.current_camera_index is not None else 'Unknown'}", 
                                   (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                        y_offset += 30
                        
                        cv2.putText(original_frame, f"Resolution: {w}x{h}", 
                                   (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                        y_offset += 30
                        
                        cv2.putText(original_frame, f"Mode: {'Image' if self.image_mode else 'Video'}", 
                                   (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                        y_offset += 30
                        
                        # 显示FPS信息
                        fps_text = f"FPS: {self.fps.fps:.1f}"
                        cv2.putText(original_frame, fps_text, 
                                   (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                        y_offset += 30
                        
                        # 显示检测统计
                        if hasattr(self, 'regions'):
                            cv2.putText(original_frame, f"Hands Detected: {len(self.regions)}", 
                                       (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                            y_offset += 30
                        
                        # 显示处理信息
                        if self.crop:
                            cv2.putText(original_frame, "Processing: Cropped", 
                                       (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
                        else:
                            cv2.putText(original_frame, "Processing: Padded", 
                                       (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
                        
                        cv2.imshow("Original Video Input", original_frame)
                    else:
                        # 如果原始视频窗口被关闭，确保它被销毁
                        try:
                            cv2.destroyWindow("Original Video Input")
                        except:
                            pass
                    key = cv2.waitKey(1) 
                    if key == ord('q') or key == 27:
                        break
                    elif key == 32:
                        # Pause on space bar
                        cv2.waitKey(0)
                    elif key == ord('1'):
                        self.show_pd_box = not self.show_pd_box
                    elif key == ord('2'):
                        self.show_pd_kps = not self.show_pd_kps
                    elif key == ord('3'):
                        self.show_rot_rect = not self.show_rot_rect
                    elif key == ord('4'):
                        self.show_landmarks = not self.show_landmarks
                    elif key == ord('5'):
                        self.show_handedness = not self.show_handedness
                    elif key == ord('6'):
                        self.show_scores = not self.show_scores
                    elif key == ord('7'):
                        self.show_gesture = not self.show_gesture
                    elif key == ord('o'):
                        # 切换原始视频窗口显示
                        self.show_original_video = not self.show_original_video
                        print(f"原始视频窗口: {'开启' if self.show_original_video else '关闭'}")
                    elif key == ord('h'):
                        # 显示帮助信息
                        self.show_help()
                except cv2.error as e:
                    if "Qt platform plugin" in str(e) or "xcb" in str(e) or "not implemented" in str(e) or "GTK" in str(e):
                        print("GUI display not available, running in headless mode...")
                        print("Press Ctrl+C to stop the program")
                        self.no_gui = True
                    else:
                        raise e
            else:
                # 无头模式运行
                import time
                time.sleep(0.1)  # 避免 CPU 占用过高

        # Print some stats
        print(f"# palm detection inferences : {nb_pd_inferences}")
        print(f"# hand landmark inferences  : {nb_lm_inferences}")
        print(f"Palm detection round trip   : {glob_pd_rtrip_time/nb_pd_inferences*1000:.1f} ms")
        print(f"Hand landmark round trip    : {glob_lm_rtrip_time/nb_lm_inferences*1000:.1f} ms")

    async def process_frame_async(self, frame_data=None):
        """异步处理单帧图像，用于WebSocket服务器"""
        try:
            if frame_data is not None:
                # 从WebSocket接收的帧数据
                nparr = np.frombuffer(frame_data, np.uint8)
                vid_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if vid_frame is None:
                    return None, None
            else:
                # 从摄像头读取
                if self.image_mode:
                    vid_frame = self.img
                else:
                    ok, vid_frame = self.cap.read()
                    if not ok:
                        return None, None

            h, w = vid_frame.shape[:2]
            if self.crop:
                # Cropping the long side to get a square shape
                self.frame_size = min(h, w)
                dx = (w - self.frame_size) // 2
                dy = (h - self.frame_size) // 2
                video_frame = vid_frame[dy:dy+self.frame_size, dx:dx+self.frame_size]
            else:
                # Padding on the small side to get a square shape
                self.frame_size = max(h, w)
                pad_h = int((self.frame_size - h)/2)
                pad_w = int((self.frame_size - w)/2)
                video_frame = cv2.copyMakeBorder(vid_frame, pad_h, pad_h, pad_w, pad_w, cv2.BORDER_CONSTANT)

            # Resize image to NN square input shape
            frame_nn = cv2.resize(video_frame, (self.pd_w, self.pd_h), interpolation=cv2.INTER_AREA)
            # Transpose hxwx3 -> 1x3xhxw
            frame_nn = np.transpose(frame_nn, (2,0,1))[None,]

            annotated_frame = video_frame.copy()

            # Get palm detection
            input_name = list(self.pd_input_tensor.names)[0] if self.pd_input_tensor.names else 'input'
            inference = self.pd_compiled_model({input_name: frame_nn})
            self.pd_postprocess(inference)
            self.pd_render(annotated_frame)

            # Hand landmarks
            if self.use_lm:
                for i,r in enumerate(self.regions):
                    frame_nn = mpu.warp_rect_img(r.rect_points, video_frame, self.lm_w, self.lm_h)
                    # Transpose hxwx3 -> 1x3xhxw
                    frame_nn = np.transpose(frame_nn, (2,0,1))[None,]
                    # Get hand landmarks
                    lm_input_name = list(self.lm_input_tensor.names)[0] if self.lm_input_tensor.names else 'input_1'
                    inference = self.lm_compiled_model({lm_input_name: frame_nn})
                    self.lm_postprocess(r, inference)
                    self.lm_render(annotated_frame, r)

            if not self.crop:
                annotated_frame = annotated_frame[pad_h:pad_h+h, pad_w:pad_w+w]

            # 准备返回数据
            hand_data = []
            if hasattr(self, 'regions'):
                for region in self.regions:
                    # 检查是否只处理右手
                    handedness = float(getattr(region, 'handedness', 0))
                    if hasattr(self, 'right_hand_only') and self.right_hand_only:
                        # 只处理右手 (handedness > 0.5 表示右手)
                        if handedness <= 0.5:
                            continue
                    
                    # 转换 landmarks 为可序列化的格式
                    landmarks = getattr(region, 'landmarks', [])
                    if landmarks:
                        # 将 NumPy 数组转换为 Python 列表
                        landmarks_serializable = []
                        for lm in landmarks:
                            if hasattr(lm, 'tolist'):
                                landmarks_serializable.append(lm.tolist())
                            else:
                                landmarks_serializable.append(list(lm))
                    else:
                        landmarks_serializable = []
                    
                    hand_info = {
                        'gesture': getattr(region, 'gesture', None),
                        'handedness': handedness,
                        'landmarks': landmarks_serializable,
                        'score': float(getattr(region, 'lm_score', 0))  # 确保是 Python float
                    }
                    hand_data.append(hand_info)
                    
                    # 处理动态手势（如果启用）
                    if dynamic_processor is not None:
                        await dynamic_processor.process_frame(hand_info, w)

            # 编码处理后的帧
            _, buffer = cv2.imencode('.jpg', annotated_frame)
            encoded_frame = base64.b64encode(buffer).decode('utf-8')

            return {
                'frame': encoded_frame,
                'hands': hand_data,
                'fps': float(getattr(self, 'fps', {}).fps if hasattr(self, 'fps') else 0)
            }, annotated_frame

        except Exception as e:
            print(f"处理帧时出错: {e}")
            return None, None

    def show_help(self):
        """显示帮助信息"""
        help_text = """
=== 手势跟踪程序控制键 ===
q 或 ESC    - 退出程序
空格        - 暂停/继续
1          - 切换手掌检测框显示
2          - 切换手掌关键点显示
3          - 切换旋转矩形显示
4          - 切换手部关键点显示
5          - 切换左右手显示
6          - 切换分数显示
7          - 切换手势识别显示
o          - 切换原始视频窗口显示
h          - 显示此帮助信息

=== 窗口说明 ===
Hand Tracking Result - 显示处理后的视频（带检测结果）
Original Video Input - 显示原始视频输入（带详细信息）
        """
        print(help_text)

# 全局HandTracker实例
ht = None

# 存储所有已连接的客户端
clients = set()

async def validate_message(data: dict) -> bool:
    """验证消息格式"""
    required_fields = ['type']
    return all(field in data for field in required_fields)

async def send_error(websocket, error_message: str):
    """发送错误消息"""
    error_response = {
        'type': 'error',
        'message': error_message,
        'timestamp': datetime.now().timestamp()
    }
    await websocket.send(json.dumps(error_response))

async def broadcast_to_others(sender_websocket, message: dict):
    """广播消息给除发送者外的所有客户端"""
    global clients
    if not clients:
        return
        
    disconnected_clients = set()
    for client in clients:
        if client != sender_websocket:
            try:
                await client.send(json.dumps(message))
            except websockets.exceptions.ConnectionClosed:
                disconnected_clients.add(client)
    
    # 清理断开的连接
    clients.difference_update(disconnected_clients)

async def handle_voice_message(websocket, data: dict):
    """处理语音消息"""
    angle = data.get('angle')
    client_name = data.get('client_name', 'unknown')
    timestamp = data.get('timestamp', datetime.now().timestamp())
    
    print(f"收到语音角度信息: {angle}° (来自 {client_name})")
    
    # 验证角度值
    if angle is None:
        print("错误：语音消息中缺少角度信息")
        return {
            'type': 'error',
            'message': '语音消息中缺少角度信息',
            'timestamp': datetime.now().timestamp()
        }
    
    try:
        # 原始输入角度可能来自环形阵列麦克风(0..360, 顺时针 360->180->0)
        # 将其映射为舵机所需的角度([-180,180]，顺时针 0->180,-180->0)
        raw_angle = float(angle)
        mapped_angle = map_mic_angle_to_servo(raw_angle)
        # 最终再夹紧，保证安全
        angle = max(-180.0, min(180.0, mapped_angle))
    except (ValueError, TypeError):
        print(f"错误：无效的角度值: {angle}")
        return {
            'type': 'error',
            'message': f'无效的角度值: {angle}',
            'timestamp': datetime.now().timestamp()
        }
    
    # 创建舵机控制任务
    task_success = False
    task_message = ""
    
    try:
        from task_queue import get_task_producer
        producer = get_task_producer()
        
        # 创建语音舵机控制任务
        success = producer.create_voice_servo_control_task(
            angle=angle,
            client_name=client_name,
            priority=0  # 高优先级
        )
        
        if success:
            print(f"语音舵机控制任务已发送到队列: 角度={angle}° (由输入 {raw_angle}° 映射)")
            task_success = True
            task_message = "任务已加入队列，舵机将开始旋转"
        else:
            print(f"发送语音舵机控制任务失败: 角度={angle}°")
            task_success = False
            task_message = "任务队列已满或正在执行其他任务，请稍后再试"
            
    except ImportError:
        print("任务队列模块未找到，跳过舵机控制")
        task_success = False
        task_message = "任务队列模块未找到"
    except Exception as e:
        print(f"语音舵机控制任务发送异常: {e}")
        task_success = False
        task_message = f"任务发送异常: {str(e)}"
    
    # 发送任务状态响应给发送者
    task_response = {
        'type': 'voice_task_status',
        'angle': angle,
        'success': task_success,
        'message': task_message,
        'timestamp': timestamp,
        'client_name': client_name
    }
    
    # 发送任务状态给发送者
    await websocket.send(json.dumps(task_response))
    
    # 广播给所有其他客户端（不包含任务状态）
    broadcast_response = {
        'type': 'voice',
        'angle': angle,
        'timestamp': timestamp,
        'client_name': client_name,
        'from_client': id(websocket)
    }
    
    # 广播语音消息给其他客户端
    await broadcast_to_others(websocket, broadcast_response)
    
    return task_response

async def handle_video_message(websocket, data: dict, message_bytes=None):
    """处理视频消息 - 兼容现有的handtracker功能"""
    global ht
    
    try:
        # 更新FPS
        if hasattr(ht, 'fps'):
            ht.fps.update()
        
        # 处理视频帧数据
        if message_bytes:
            # 从二进制数据解码视频帧
            nparr = np.frombuffer(message_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if frame is not None:
                # 进行手部跟踪和手势识别
                result, annotated_frame = await ht.process_frame_async(message_bytes)
                
                if result is not None:
                    # 更新FPS信息
                    result['fps'] = float(ht.fps.fps if hasattr(ht, 'fps') else 0)
                    result['type'] = 'video_result'  # 添加消息类型标识
                    return result
                else:
                    # 发送空结果
                    return {
                        'type': 'video_result',
                        'frame': '',
                        'hands': [],
                        'fps': float(ht.fps.fps if hasattr(ht, 'fps') else 0)
                    }
            else:
                print("无法解码视频帧")
                return None
        else:
            print("没有视频数据")
            return None
            
    except Exception as e:
        print(f"处理视频帧时出错: {e}")
        return {
            'type': 'video_result',
            'frame': '',
            'hands': [],
            'fps': float(ht.fps.fps if hasattr(ht, 'fps') else 0),
            'error': str(e)
        }

async def handle_ping_message(websocket, data: dict):
    """处理心跳消息"""
    timestamp = data.get('timestamp', datetime.now().timestamp())
    client_name = data.get('client_name', 'unknown')
    
    print(f"收到心跳消息 (来自 {client_name})")
    
    # 回复 pong
    pong_response = {
        'type': 'pong',
        'timestamp': datetime.now().timestamp(),
        'original_timestamp': timestamp
    }
    await websocket.send(json.dumps(pong_response))
    return pong_response

async def handtracker_websocket_handler(websocket):
    """基于消息类型的WebSocket处理器 - 支持handtracker和voice客户端"""
    global ht, clients
    client_id = id(websocket)
    clients.add(websocket)
    print(f"客户端连接: {websocket.remote_address} (ID: {client_id})")
    
    try:
        # 发送初始配置信息
        config = {
            'type': 'system',
            'message': 'HandTracker WebSocket Server Ready',
            'gesture_support': ht.use_gesture if ht else False,
            'landmark_support': ht.use_lm if ht else False,
            'client_id': client_id,
            'timestamp': datetime.now().timestamp()
        }
        await websocket.send(json.dumps(config))
        
        # 处理消息流
        async for message in websocket:
            try:
                # 尝试解析 JSON 消息
                if isinstance(message, (bytes, bytearray)):
                    # 二进制数据 - 可能是视频帧
                    result = await handle_video_message(websocket, {}, message)
                    if result:
                        await websocket.send(json.dumps(result))
                else:
                    # JSON 消息
                    try:
                        data = json.loads(message)
                        
                        # 验证消息格式
                        if not await validate_message(data):
                            await send_error(websocket, "Invalid message format: missing required fields")
                            continue
                        
                        message_type = data.get('type', 'unknown')
                        
                        # 根据消息类型路由
                        if message_type == 'video':
                            # 处理视频消息（兼容现有handtracker客户端）
                            result = await handle_video_message(websocket, data)
                            if result:
                                await websocket.send(json.dumps(result))
                        elif message_type == 'voice':
                            # 处理语音消息
                            result = await handle_voice_message(websocket, data)
                            # 可以选择是否回复
                        elif message_type == 'ping':
                            # 处理心跳消息
                            await handle_ping_message(websocket, data)
                        else:
                            await send_error(websocket, f"Unknown message type: {message_type}")
                            
                    except json.JSONDecodeError:
                        # 如果不是 JSON，可能是二进制数据（如视频帧）
                        result = await handle_video_message(websocket, {}, message)
                        if result:
                            await websocket.send(json.dumps(result))
                    
            except websockets.exceptions.ConnectionClosed:
                print("客户端断开连接")
                break
            except Exception as e:
                print(f"处理消息时出错: {e}")
                # 发送错误信息
                error_result = {
                    'type': 'error',
                    'message': str(e),
                    'timestamp': datetime.now().timestamp()
                }
                try:
                    await websocket.send(json.dumps(error_result))
                except:
                    break
                
    except websockets.exceptions.ConnectionClosed:
        print(f"客户端断开连接 (ID: {client_id})")
    except Exception as e:
        print(f"WebSocket连接错误: {e}")
    finally:
        clients.discard(websocket)  # 清理客户端连接
        print(f"WebSocket连接已关闭 (ID: {client_id})")

async def initialize_handtracker():
    """初始化HandTracker和动态手势处理器"""
    global ht, dynamic_processor
    
    # 检查是否有全局args
    import sys
    current_module = sys.modules[__name__]
    if not hasattr(current_module, 'args') or current_module.args is None:
        raise RuntimeError("HandTracker未初始化，请先设置args参数")
    
    args = current_module.args
    
    # 初始化动态手势处理器
    if args.dynamic_gestures:
        dynamic_processor = DynamicGestureProcessor(
            window_size=args.gesture_window_size,
            min_confidence=args.gesture_confidence,
            enable_wave_detection=args.enable_wave_detection,
            wave_threshold=args.wave_threshold,
            wave_min_movement=args.wave_min_movement,
            wave_window_size=args.wave_window_size
        )
        print(f"动态手势处理已启用 - 窗口大小: {args.gesture_window_size}, 置信度阈值: {args.gesture_confidence}")
        print(f"挥手检测: {'启用' if args.enable_wave_detection else '禁用'}")
        if args.enable_wave_detection:
            print(f"挥手检测参数 - 阈值比例: {args.wave_threshold}, 最小移动: {args.wave_min_movement}px")
            print(f"滑动窗口大小: {args.wave_window_size} 帧")
    else:
        dynamic_processor = None
        print("动态手势处理已禁用")
    
    # 处理显示控制参数
    show_pd_box = args.show_pd_box if not args.hide_pd_box else False
    show_pd_kps = args.show_pd_kps if not args.hide_pd_kps else False
    show_rot_rect = args.show_rot_rect if not args.hide_rot_rect else False
    show_landmarks = args.show_landmarks if not args.hide_landmarks else False
    show_handedness = args.show_handedness if not args.hide_handedness else False
    show_scores = args.show_scores if not args.hide_scores else False
    show_gesture_display = args.show_gesture_display if not args.hide_gesture_display else False
    show_original_video = args.show_original_video if not args.hide_original_video else False
    
    # 创建HandTracker实例
    ht = HandTracker(input_src=args.input, 
                    pd_device=args.pd_device, 
                    use_lm= not args.no_lm, 
                    lm_device=args.lm_device,
                    use_gesture=args.gesture,
                    crop=args.crop,
                    no_gui=True,  # WebSocket模式下强制无头模式
                    right_hand_only=args.right_hand_only,
                    show_pd_box=show_pd_box,
                    show_pd_kps=show_pd_kps,
                    show_rot_rect=show_rot_rect,
                    show_landmarks=show_landmarks,
                    show_handedness=show_handedness,
                    show_scores=show_scores,
                    show_gesture_display=show_gesture_display,
                    show_original_video=show_original_video)
    
    print("HandTracker初始化完成")
    print("\n=== 当前显示设置 ===")
    print(f"手掌检测框: {'开启' if ht.show_pd_box else '关闭'}")
    print(f"手掌关键点: {'开启' if ht.show_pd_kps else '关闭'}")
    print(f"旋转矩形: {'开启' if ht.show_rot_rect else '关闭'}")
    print(f"手部关键点: {'开启' if ht.show_landmarks else '关闭'}")
    print(f"左右手显示: {'开启' if ht.show_handedness else '关闭'}")
    print(f"分数显示: {'开启' if ht.show_scores else '关闭'}")
    print(f"手势识别: {'开启' if ht.show_gesture else '关闭'}")
    print(f"原始视频窗口: {'开启' if ht.show_original_video else '关闭'}")
    print(f"右手检测: {'开启' if ht.right_hand_only else '关闭'}")
    print("=" * 30)

async def main():
    global ht, dynamic_processor
    
    # 初始化HandTracker和动态手势处理器
    await initialize_handtracker()
    
    # 启动WebSocket服务器
    print("启动手部跟踪WebSocket服务器...")
    print("服务器地址: ws://0.0.0.0:8765")
    print("功能: 接收客户端视频流，进行手势识别，返回识别结果")
    
    # 初始化舵机控制器
    if args.enable_servo:
        try:
            from servo_controller import initialize_servo_controller
            servo_controller = initialize_servo_controller(
                port=args.servo_port,      # 舵机串口
                baudrate=args.servo_baudrate,  # 波特率
                servo_id=args.servo_id     # 舵机ID
            )
            if servo_controller.is_connected:
                print("✓ 舵机控制器初始化成功")
            else:
                print("⚠ 舵机控制器初始化失败，将跳过舵机控制功能")
        except Exception as e:
            print(f"⚠ 舵机控制器初始化异常: {e}")
            print("将跳过舵机控制功能")
    else:
        print("舵机控制功能已禁用")
    
    # 启动WebSocket服务器
    async with websockets.serve(handtracker_websocket_handler, "0.0.0.0", 8765):
        print("手部跟踪WebSocket服务器已启动，等待连接...")
        await asyncio.Future()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--input', type=str, default='0', 
                        help="Path to video or image file to use as input (default=%(default)s)")
    parser.add_argument('-g', '--gesture', action="store_true", 
                        help="enable gesture recognition")
    parser.add_argument("--pd_m", default="models/palm_detection_FP32.xml", type=str,
                        help="Path to an .xml file for palm detection model (default=%(default)s)")
    parser.add_argument("--pd_device", default='GPU', type=str,
                        help="Target device for the palm detection model (default=%(default)s)")  
    parser.add_argument('--no_lm', action="store_true", 
                        help="only the palm detection model is run, not the hand landmark model")
    parser.add_argument("--lm_m", default="models/hand_landmark_FP32.xml", type=str,
                        help="Path to an .xml file for landmark model (default=%(default)s)")
    parser.add_argument("--lm_device", default='GPU', type=str,
                        help="Target device for the landmark regression model (default=%(default)s)")
    parser.add_argument('-c', '--crop', action="store_true", 
                        help="center crop frames to a square shape before feeding palm detection model")
    parser.add_argument('--no_gui', action="store_true", 
                        help="run in headless mode without GUI display")
    parser.add_argument('--right_hand_only', action="store_true", 
                        help="only process right hand gestures")
    
    # 显示控制参数
    parser.add_argument('--show_pd_box', action="store_true", 
                        help="show palm detection boxes")
    parser.add_argument('--show_pd_kps', action="store_true", 
                        help="show palm detection keypoints")
    parser.add_argument('--show_rot_rect', action="store_true", 
                        help="show rotation rectangles")
    parser.add_argument('--show_landmarks', action="store_true", 
                        help="show hand landmarks")
    parser.add_argument('--show_handedness', action="store_true", 
                        help="show left/right hand indication")
    parser.add_argument('--show_scores', action="store_true", 
                        help="show detection and landmark scores")
    parser.add_argument('--show_gesture_display', action="store_true", 
                        help="show gesture recognition results")
    parser.add_argument('--show_original_video', action="store_true", 
                        help="show original video window")
    
    # 关闭显示的参数
    parser.add_argument('--hide_pd_box', action="store_true", 
                        help="hide palm detection boxes")
    parser.add_argument('--hide_pd_kps', action="store_true", 
                        help="hide palm detection keypoints")
    parser.add_argument('--hide_rot_rect', action="store_true", 
                        help="hide rotation rectangles")
    parser.add_argument('--hide_landmarks', action="store_true", 
                        help="hide hand landmarks")
    parser.add_argument('--hide_handedness', action="store_true", 
                        help="hide left/right hand indication")
    parser.add_argument('--hide_scores', action="store_true", 
                        help="hide detection and landmark scores")
    parser.add_argument('--hide_gesture_display', action="store_true", 
                        help="hide gesture recognition results")
    parser.add_argument('--hide_original_video', action="store_true", 
                        help="hide original video window")
    
    # 动态手势处理参数
    parser.add_argument('--dynamic_gestures', action="store_true", 
                        help="enable dynamic gesture recognition")
    parser.add_argument('--gesture_window_size', type=int, default=8,
                        help="sliding window size for dynamic gesture detection (default=%(default)s)")
    parser.add_argument('--gesture_confidence', type=float, default=0.6,
                        help="minimum confidence threshold for dynamic gestures (default=%(default)s)")
    parser.add_argument('--enable_wave_detection', action="store_true", default=False,
                        help="enable wave gesture detection (default=%(default)s)")
    parser.add_argument('--wave_threshold', type=float, default=0.2,
                        help="wave detection threshold ratio (default=%(default)s)")
    parser.add_argument('--wave_min_movement', type=int, default=50,
                        help="minimum movement in pixels for wave detection (default=%(default)s)")
    parser.add_argument('--wave_window_size', type=int, default=5,
                        help="sliding window size for wave detection (default=%(default)s)")
    
    # 舵机控制参数
    parser.add_argument('--servo_port', type=str, default='/dev/ttyUSB0',
                        help="servo serial port (default=%(default)s)")
    parser.add_argument('--servo_baudrate', type=int, default=115200,
                        help="servo baudrate (default=%(default)s)")
    parser.add_argument('--servo_id', type=int, default=0,
                        help="servo ID (default=%(default)s)")
    parser.add_argument('--enable_servo', action="store_true", default=False,
                        help="enable servo control")

    args = parser.parse_args()
    asyncio.run(main())

