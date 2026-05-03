import json
import os
from datetime import datetime, timezone, timedelta

BUFFER_FILE = '/root/gold-signals/pipeline/signal_buffer.json'
MAX_SAME_SIGNAL = 2
WINDOW_MINUTES = 30

def load_buffer():
    if os.path.exists(BUFFER_FILE):
        try:
            with open(BUFFER_FILE) as f:
                return json.load(f)
        except:
            pass
    return []

def save_buffer(buf):
    tmp = BUFFER_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(buf, f)
    os.replace(tmp, BUFFER_FILE)

def is_duplicate(signal):
    buf = load_buffer()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=WINDOW_MINUTES)).isoformat()
    
    # Clean old entries
    buf = [b for b in buf if b['time'] > cutoff]
    
    # Check count of same signal
    zone_top = signal.get('zone_top', 0)
    tp1 = signal.get('tp1', 0)
    
    count = sum(1 for b in buf 
                if abs(b.get('zone_top', 0) - zone_top) < 0.5 
                and b.get('tp1') == tp1)
    
    if count >= MAX_SAME_SIGNAL:
        print(f'BUFFER: Signal rejected (count={count}) zone_top={zone_top} tp1={tp1}')
        save_buffer(buf)
        return True
    
    # Add to buffer
    buf.append({
        'time': now.isoformat(),
        'zone_top': zone_top,
        'tp1': tp1
    })
    save_buffer(buf)
    return False

if __name__ == '__main__':
    buf = load_buffer()
    print(f'Buffer entries: {len(buf)}')
    for b in buf:
        print(f'  {b["time"][11:19]} zone={b["zone_top"]} tp1={b["tp1"]}')
