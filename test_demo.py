import asyncio
import tempfile
import os
from pathlib import Path
from src.bus.event_bus import EventBus
from src.agent.orchestrator import Orchestrator
from src.monitor.file_watcher import FileWatcher

async def test_basic_flow():
    """测试基础流程"""
    print("=== AutoFix Agent 基础流程测试 ===")
    
    # 1. 创建临时日志文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False, encoding='utf-8') as f:
        log_file = f.name
    
    try:
        # 2. 初始化组件
        event_bus = EventBus()
        orchestrator = Orchestrator()
        
        # 3. 订阅事件处理
        async def handle_event(event):
            print(f"\n收到新错误事件: {event.error_type}")
            print(f"错误信息: {event.error_message}")
            if event.error_location:
                print(f"错误位置: {event.error_location.file}:{event.error_location.line}")
            
            # 交给Orchestrator处理
            result = await orchestrator.process_event(event)
            print(f"\n处理结果: {result.status}")
            print(f"根因分析: {result.context.get('root_cause', '未知')}")
        
        await event_bus.subscribe(handle_event)
        
        # 4. 启动文件监控
        watcher = FileWatcher(log_file, event_bus)
        watcher_task = asyncio.create_task(watcher.start())
        
        # 启动事件消费
        consumer_task = asyncio.create_task(event_bus.start_consuming())
        
        await asyncio.sleep(1)
        print(f"\n监控已启动，日志文件: {log_file}")
        
        # 5. 写入错误日志
        print("\n写入测试错误日志...")
        test_traceback = '''Traceback (most recent call last):
  File "test_service.py", line 25, in process_order
    discount = calculate_discount(total, 0)
  File "test_service.py", line 10, in calculate_discount
    return total / discount_rate
ZeroDivisionError: division by zero
'''
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(test_traceback + '\n')
        
        await asyncio.sleep(2)
        print("\n✅ 测试完成！")
        
    finally:
        # 清理
        os.unlink(log_file)
        watcher_task.cancel()
        consumer_task.cancel()
        try:
            await watcher_task
            await consumer_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    asyncio.run(test_basic_flow())
