# action.py
from multiprocessing.managers import BaseManager

class QueueManager(BaseManager):
    pass

QueueManager.register("get_queue")
QueueManager.register("is_busy_event")

manager = QueueManager(address=("localhost", 50000), authkey=b"abc")
manager.connect()

queue = manager.get_queue()
is_busy_event = manager.is_busy_event()

# 每个动作独立编号
task_counters = {"A": 0, "B": 0, "C": 0}

print("👉 命令说明：")
print("   A / B / C    # 代表三种不同类型的任务")
print("   quit         # 退出程序\n")

while True:
    cmd = input("指令: ").strip().upper()
    if cmd == "QUIT":
        break

    elif cmd in task_counters:
        # 检查消费者是否正在执行任务
        if is_busy_event.is_set():
            print(f"[{cmd}] ❌ 消费者正在执行任务，请稍后再试")
        elif queue.full():
            print(f"[{cmd}] ❌ 队列已满，请稍后再试")
        else:
            task_counters[cmd] += 1
            task_id = task_counters[cmd]
            queue.put((cmd, task_id))
            print(f"[{cmd}] ✅ 成功放入任务 {task_id}")
    else:
        print("⚠️ 无效指令，只能输入 A / B / C / quit")
