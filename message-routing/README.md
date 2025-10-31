# WebSocket 基于消息类型路由示例

这个示例展示了如何正确实现 WebSocket 的多端点功能，通过**基于消息类型的路由**而不是多连接。

## 核心概念

WebSocket 连接本质上是一个**单一的、持久的通道**。要实现"多端点"功能，应该在**消息内容**中实现路由逻辑，而不是创建多个连接。

## 文件说明

### 服务器端
- `correct_server.py` - 改进的服务器实现，支持基于消息类型的路由
- `server_legacy.py` - 原始的多连接实现（不推荐）

### 客户端示例
- `voice_client.py` - 语音客户端，发送角度数据
- `video_client.py` - 视频客户端，发送视频帧数据
- `chat_client.py` - 聊天客户端，发送聊天消息
- `correct_client.py` - 综合客户端，发送多种类型消息

## 使用方法

### 1. 启动服务器
```bash
python correct_server.py
```

### 2. 运行客户端（可以同时运行多个）
```bash
# 终端1 - 语音客户端
python voice_client.py

# 终端2 - 视频客户端  
python video_client.py

# 终端3 - 聊天客户端
python chat_client.py
```

## 消息格式

所有消息都通过同一个 WebSocket 连接发送，格式如下：

### 语音消息
```json
{
    "type": "voice",
    "angle": 45,
    "timestamp": 1234567890.123,
    "client_name": "voice_client"
}
```

### 视频消息
```json
{
    "type": "video", 
    "data": {
        "frame_id": 1,
        "width": 640,
        "height": 480,
        "frame_data": "<base64>"
    },
    "timestamp": 1234567890.123,
    "client_name": "video_client"
}
```

### 聊天消息
```json
{
    "type": "chat",
    "message": "Hello World!",
    "timestamp": 1234567890.123,
    "client_name": "chat_client"
}
```

### 心跳消息
```json
{
    "type": "ping",
    "timestamp": 1234567890.123,
    "client_name": "client_name"
}
```

## 优势

1. **单一连接** - 减少资源消耗和连接管理复杂度
2. **类型安全** - 通过消息类型字段进行明确的路由
3. **扩展性好** - 容易添加新的消息类型
4. **跨功能通信** - 不同功能之间可以轻松共享数据
5. **符合 WebSocket 设计理念** - 利用持久连接的特性

## 错误处理

服务器包含完整的错误处理机制：
- 消息格式验证
- 未知消息类型处理
- 连接断开自动清理
- 详细的错误响应

## 与多连接方式的对比

| 特性 | 基于消息类型路由 | 多连接方式 |
|------|------------------|------------|
| 连接数 | 1个 | N个 |
| 资源消耗 | 低 | 高 |
| 跨功能通信 | 容易 | 困难 |
| 连接管理 | 简单 | 复杂 |
| 扩展性 | 好 | 一般 |
| 符合 WebSocket 理念 | ✅ | ❌ |
