# worker.py
import multiprocessing
import time

def consumer(queue, is_busy_event):
    while True:
        if not queue.empty():
            producer_name, task_id = queue.get()
            # 设置事件：标记为忙碌状态
            is_busy_event.set()
            print(f"[消费者] 开始执行任务: {producer_name} 的任务 {task_id}")
            time.sleep(5)  # 模拟耗时任务
            print(f"[消费者] 完成任务: {producer_name} 的任务 {task_id}")
            # 清除事件：标记为空闲状态
            is_busy_event.clear()
        else:
            time.sleep(0.1)  # 空闲时短暂休眠，防止CPU占用过高

if __name__ == "__main__":
    from multiprocessing.managers import BaseManager

    manager = multiprocessing.Manager()
    queue = manager.Queue(maxsize=1)  # 严格顺序：一次只能有一个任务
    is_busy_event = multiprocessing.Event()  # 使用 Event 替代 Value

    class QueueManager(BaseManager):
        pass

    QueueManager.register("get_queue", callable=lambda: queue)
    QueueManager.register("is_busy_event", callable=lambda: is_busy_event)

    manager_server = QueueManager(address=("localhost", 50000), authkey=b"abc")
    
    # 启动消费者进程
    print("[消费者] 正在启动...")
    consumer_process = multiprocessing.Process(target=consumer, args=(queue, is_busy_event))
    consumer_process.start()
    
    # 启动服务器（会阻塞）
    manager_server.get_server().serve_forever()
