import subprocess
import sys
import os
import time

scripts = [
    '/root/gold-signals/pipeline/candle_daemon.py',
    '/root/gold-signals/pipeline/telegram_listener.py',
    '/root/gold-signals/pipeline/price_daemon.py',
    '/root/gold-signals/pipeline/strategy_b.py',
    '/root/gold-signals/pipeline/strategy_c.py',
    '/root/gold-signals/pipeline/strategy_d.py'
]

processes = []
for script in scripts:
    p = subprocess.Popen(
        [sys.executable, script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    processes.append(p)
    print(f'Started: {os.path.basename(script)} (pid={p.pid})')
    time.sleep(5)

print('All scripts running — check Telegram for startup messages')

try:
    for p in processes:
        p.wait()
except KeyboardInterrupt:
    print('Shutting down...')
    for p in processes:
        p.terminate()
