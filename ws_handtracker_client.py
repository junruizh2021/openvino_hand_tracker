#!/usr/bin/env python3
"""
WebSocket客户端 - 发送视频到服务器并接收手部跟踪结果
"""

import asyncio
import websockets
import json
import cv2
import base64
import numpy as np
import argparse

async def send_video_and_receive_results(enable_ui=False):
    """发送视频到服务器并接收手部跟踪结果"""
    uri = "ws://localhost:8765"
    cap = None  # 初始化cap变量
    
    try:
        async with websockets.connect(uri, max_size=None) as websocket:
            print("已连接到服务器，准备发送视频并接收结果")
            
            # 打开摄像头
            cap = cv2.VideoCapture(2)
            if not cap.isOpened():
                print("无法打开摄像头")
                return
            
            # 获取摄像头参数
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            #width = 640
            #height = 480
            fps = int(cap.get(cv2.CAP_PROP_FPS)) or 60
            
            # 发送视频元数据（兼容新的消息路由系统）
            metadata = {
                "type": "video",
                "width": width,
                "height": height,
                "fps": fps,
                "mode": "handtracking",  # 指定为手部跟踪模式
                "timestamp": asyncio.get_event_loop().time()
            }
            
            await websocket.send(json.dumps(metadata))
            print(f"发送视频元数据: {width}x{height}, {fps}fps")
            
            frame_count = 0
            
            # 创建任务来处理接收和发送
            async def send_frames():
                nonlocal frame_count
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    
                    # 编码帧
                    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    frame_bytes = buffer.tobytes()
                    
                    # 发送帧数据
                    await websocket.send(frame_bytes)
                    frame_count += 1
                    
                    # 控制发送频率
                    await asyncio.sleep(1.0/fps)
            
            async def receive_results():
                while True:
                    try:
                        # 接收服务器返回的结果
                        data = await websocket.recv()
                        result = json.loads(data)
                        
                        # 解码处理后的图像（如果启用UI）
                        if enable_ui and 'frame' in result:
                            frame_data = base64.b64decode(result['frame'])
                            nparr = np.frombuffer(frame_data, np.uint8)
                            processed_frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                            
                            if processed_frame is not None:
                                # 显示处理后的图像
                                cv2.imshow("Hand Tracking Result", processed_frame)
                                
                                # 按 'q' 退出
                                if cv2.waitKey(1) & 0xFF == ord('q'):
                                    break
                            else:
                                print("无法解码处理后的图像")
                        
                        # 处理不同类型的响应
                        message_type = result.get('type', 'unknown')
                        
                        if message_type == 'video_result':
                            # 处理视频处理结果
                            if 'hands' in result and result['hands']:
                                print(f"帧 {result.get('frame_id', 0)}: 检测到 {len(result['hands'])} 只手")
                                for i, hand in enumerate(result['hands']):
                                    print(f"  手 {i+1}: 手势={hand.get('gesture', 'None')}, "
                                          f"左右手={hand.get('handedness', 'Unknown')}, "
                                          f"置信度={hand.get('score', 0):.2f}")
                            
                            # 显示FPS
                            if 'fps' in result:
                                print(f"服务器FPS: {result['fps']:.1f}")
                                
                        elif message_type == 'system':
                            # 处理系统消息
                            print(f"系统消息: {result.get('message', '')}")
                            
                        elif message_type == 'voice':
                            # 处理语音消息（来自其他客户端）
                            angle = result.get('angle', 0)
                            client_name = result.get('client_name', 'unknown')
                            print(f"收到语音数据: 角度={angle}° (来自 {client_name})")
                            
                        elif message_type == 'error':
                            # 处理错误消息
                            print(f"错误: {result.get('message', '')}")
                            
                        else:
                            # 兼容旧格式（向后兼容）
                            if 'hands' in result and result['hands']:
                                print(f"帧 {result.get('frame_id', 0)}: 检测到 {len(result['hands'])} 只手")
                                for i, hand in enumerate(result['hands']):
                                    print(f"  手 {i+1}: 手势={hand.get('gesture', 'None')}, "
                                          f"左右手={hand.get('handedness', 'Unknown')}, "
                                          f"置信度={hand.get('score', 0):.2f}")
                            
                            if 'fps' in result:
                                print(f"服务器FPS: {result['fps']:.1f}")
                                
                    except websockets.exceptions.ConnectionClosed:
                        print("服务器连接已关闭")
                        break
                    except Exception as e:
                        print(f"接收数据时出错: {e}")
                        break
            
            # 同时运行发送和接收任务
            try:
                await asyncio.gather(
                    send_frames(),
                    receive_results()
                )
            except KeyboardInterrupt:
                print("用户中断")
            except Exception as e:
                print(f"运行出错: {e}")
                    
    except Exception as e:
        print(f"连接服务器失败: {e}")
    finally:
        if cap is not None:
            cap.release()
        if enable_ui:
            cv2.destroyAllWindows()

if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='WebSocket客户端 - 发送视频并接收手部跟踪结果')
    parser.add_argument('--enable-UI', action='store_true', 
                       help='启用UI界面显示处理后的视频帧')
    args = parser.parse_args()
    
    print("WebSocket客户端 - 发送视频并接收手部跟踪结果")
    if args.enable_UI:
        print("UI模式：按 'q' 键退出")
    else:
        print("无UI模式：按 Ctrl+C 退出")
    
    asyncio.run(send_video_and_receive_results(enable_ui=args.enable_UI))
