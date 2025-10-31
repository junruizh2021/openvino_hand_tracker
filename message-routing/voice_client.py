import asyncio
import json
import websockets
import time
import sys

async def voice_client():
    """语音客户端 - 用户输入角度数据"""
    uri = "ws://localhost:8765"
    
    async with websockets.connect(uri) as websocket:
        print("🎤 语音客户端已连接到服务器")
        print("💡 输入角度值 (-180 到 180) 来控制舵机0")
        print("💡 输入 'q' 或 'quit' 退出程序")
        print("💡 输入 'help' 查看帮助信息")
        print("-" * 50)
        
        try:
            # 监听服务器消息
            async def listen_for_messages():
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        msg_type = data.get('type')
                        
                        if msg_type == 'system':
                            print(f"📢 系统消息: {data.get('message')}")
                        elif msg_type == 'voice':
                            angle = data.get('angle')
                            client_name = data.get('client_name', 'unknown')
                            task_success = data.get('task_success')
                            task_message = data.get('task_message', '')
                            
                            if task_success is not None:
                                if task_success:
                                    print(f"🔊 语音数据: 角度={angle}° (来自 {client_name}) ✅ 任务已加入队列")
                                else:
                                    print(f"🔊 语音数据: 角度={angle}° (来自 {client_name}) ❌ 任务加入失败: {task_message}")
                            else:
                                print(f"🔊 收到语音数据: 角度={angle}° (来自客户端 {data.get('from_client')})")
                        elif msg_type == 'voice_task_status':
                            # 这是发送者收到的任务状态响应
                            angle = data.get('angle')
                            success = data.get('success')
                            message_text = data.get('message', '')
                            
                            if success:
                                print(f"✅ 任务状态: 角度={angle}° - {message_text}")
                                print(f"⏳ 舵机0正在旋转到 {angle}°...")
                            else:
                                print(f"❌ 任务状态: 角度={angle}° - {message_text}")
                                print(f"💡 提示: 可能正在执行其他舵机任务，请稍后再试")
                        elif msg_type == 'error':
                            print(f"❌ 错误: {data.get('message')}")
                        else:
                            print(f"📨 收到消息: {data}")
                    except json.JSONDecodeError:
                        print(f"📦 收到二进制数据，大小: {len(message)}")
            
            # 启动消息监听任务
            listen_task = asyncio.create_task(listen_for_messages())
            
            # 用户输入处理
            while True:
                try:
                    # 获取用户输入
                    user_input = input("\n🎯 请输入角度 (或输入 'help' 查看帮助): ").strip()
                    
                    # 处理特殊命令
                    if user_input.lower() in ['q', 'quit', 'exit']:
                        print("👋 退出程序...")
                        break
                    elif user_input.lower() == 'help':
                        print_help()
                        continue
                    elif user_input.lower() == 'status':
                        print("📊 当前状态: 已连接到服务器")
                        continue
                    
                    # 尝试解析角度值
                    try:
                        angle = float(user_input)
                        
                        # 验证角度范围
                        if angle < -180 or angle > 180:
                            print(f"❌ 角度超出范围！请输入 -180 到 180 之间的值")
                            continue
                        
                        # 发送语音消息
                        voice_message = {
                            'type': 'voice',
                            'angle': angle,
                            'timestamp': time.time(),
                            'client_name': 'terminal_client'
                        }
                        
                        await websocket.send(json.dumps(voice_message))
                        print(f"📤 发送语音数据: 角度={angle}°")
                        print(f"⏳ 舵机0正在旋转到 {angle}°...")
                        
                    except ValueError:
                        print(f"❌ 无效的角度值: {user_input}")
                        print("💡 请输入数字或 'help' 查看帮助")
                        continue
                        
                except KeyboardInterrupt:
                    print("\n👋 收到中断信号，退出程序...")
                    break
                except EOFError:
                    print("\n👋 输入结束，退出程序...")
                    break
                
        except websockets.exceptions.ConnectionClosed:
            print("🔌 与服务器的连接已关闭")
        except Exception as e:
            print(f"❌ 发生错误: {e}")
        finally:
            listen_task.cancel()

def print_help():
    """打印帮助信息"""
    print("\n" + "="*50)
    print("🎯 语音舵机控制客户端 - 帮助信息")
    print("="*50)
    print("📝 命令说明:")
    print("  • 输入数字: 控制舵机0旋转到指定角度")
    print("  • 角度范围: -180° 到 180°")
    print("  • help: 显示此帮助信息")
    print("  • status: 显示连接状态")
    print("  • q/quit/exit: 退出程序")
    print("\n📋 示例:")
    print("  • 输入 '45'  - 舵机旋转到45度")
    print("  • 输入 '-90' - 舵机旋转到-90度")
    print("  • 输入 '0'   - 舵机回到0度位置")
    print("\n⚠️  注意事项:")
    print("  • 确保舵机进程正在运行")
    print("  • 舵机运动需要时间，请等待完成")
    print("  • 使用 Ctrl+C 可以强制退出")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(voice_client())
