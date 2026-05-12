from signal_buffer import is_duplicate
import asyncio
import json
import os
import re
from datetime import datetime, timezone
from telethon import TelegramClient, events

api_id     = 29091418
api_hash   = '3d1d32748831ac3d51991d24d03623bd'
channel_id = -1003293033145

QUEUE_FILE  = '/root/gold-signals/pipeline/signal_queue.json'
LOG_FILE    = '/root/gold-signals/pipeline/message_log.json'
CANCEL_FILE = '/root/gold-signals/pipeline/cancel_flag.json'
LAST_ID_FILE = '/root/gold-signals/pipeline/last_msg_id.json'

CANCEL_KEYWORDS = [
    'CANCEL', 'CANCELLED', 'DISREGARD',
    'IGNORE THAT', 'IGNORE LAST', 'DELETE'
]

IGNORE_KEYWORDS = [
    'RISK FREE', 'SL TO BE', 'CLOSE +', 'SECURING',
    'PARTIAL', 'TP1 HIT', 'TP2 HIT', 'TP3 HIT',
    'SL HIT', 'BREAKEVEN', 'MOVE SL', 'LOCKED IN',
    'TRAILING', 'AT YOUR OWN RISK', 'PIPS',
    'RECAP', 'TRADERS', 'BACK IN RANGE',
    'PERFECT CALL', 'INSANE', 'AND THERE IT IS',
    'ZONE FAILED', 'IF PRICE', 'DISPLAY OF'
]

BUY_KEYWORDS     = ['BUY GOLD', 'BUY XAUUSD']
SELL_KEYWORDS    = ['SELL GOLD', 'SELL XAUUSD']
LIMIT_KEYWORDS   = ['BUY LIMITS', 'SELL LIMITS', 'BUY LIMIT', 'SELL LIMIT']
PREPARE_KEYWORDS = ['PREPARE FOR BUY', 'PREPARE FOR SELL', 'PREPARE']

OUT_OF_OFFICE_KEYWORDS = [
    'OUT OF OFFICE', 'NOT AROUND', 'WONT BE AROUND',
    'TRADE AT YOUR OWN RISK', 'AWAY', 'WALK THE DOG',
    'OFF TO', 'MAY NOT BE AROUND'
]

STALE_SECONDS = 120

def classify_message(text):
    if not text:
        return 'ignore'
    upper = text.upper()
    for kw in CANCEL_KEYWORDS:
        if kw in upper and len(text) < 50:
            return 'cancel'
    for kw in IGNORE_KEYWORDS:
        if kw in upper:
            return 'ignore'
    for kw in PREPARE_KEYWORDS:
        if kw in upper:
            return 'prepare'
    for kw in LIMIT_KEYWORDS:
        if kw in upper:
            if 'BUY' in upper:
                return 'buy_limit'
            return 'sell_limit'
    if any(kw in upper for kw in BUY_KEYWORDS) and 'SL' in upper and 'TP' in upper:
        return 'buy'
    if any(kw in upper for kw in SELL_KEYWORDS) and 'SL' in upper and 'TP' in upper:
        return 'sell'
    if any(kw in upper for kw in ['RISK FREE', 'MOVE SL', 'SL TO BE', 'SL TO']) and any(c.isdigit() for c in text):
        return 'sl_manage'
    if any(kw in upper for kw in ['OUT AT ENTRY', 'OUT AT BREAKEVEN', 'CLOSED ALL', 'EXIT ALL', 'OUT OF ALL']):
        return 'exit_all'
    return 'ignore'

def parse_signal(text, msg_type, msg_time, age_seconds):
    try:
        zone = re.search(r'(\d{4}\.?\d*)/(\d{4}\.?\d*)', text)
        tps  = re.findall(r'TP\s+(\d{4}\.?\d*)', text)
        sl   = re.search(r'SL\s+(\d{4}\.?\d*)', text)
        if not (zone and tps and sl):
            return None
        zt = float(zone.group(1))
        zb = float(zone.group(2))
        zone_top = max(zt, zb)
        zone_bot = min(zt, zb)
        tp_levels = [float(t) for t in tps]
        sl_val = float(sl.group(1))
        upper = text.upper()
        out_of_office = any(kw in upper for kw in OUT_OF_OFFICE_KEYWORDS)
        high_risk = 'HIGH RISK' in upper
        zone_width = round(zone_top - zone_bot, 2)
        zone_70pct = round(zone_bot + zone_width * 0.70, 2)
        fill_trigger = round(zone_top + 0.15, 2)
        return {
            'type': msg_type,
            'direction': 'BUY' if 'buy' in msg_type else 'SELL',
            'zone_top': zone_top,
            'zone_bot': zone_bot,
            'zone_width': zone_width,
            'zone_70pct': zone_70pct,
            'fill_trigger': fill_trigger,
            'tp1': tp_levels[0],
            'tp2': tp_levels[1] if len(tp_levels) > 1 else None,
            'tp3': tp_levels[2] if len(tp_levels) > 2 else None,
            'sl': sl_val,
            'high_risk': high_risk,
            'out_of_office': out_of_office,
            'time': msg_time,
            'age_seconds': age_seconds,
            'stale': age_seconds > STALE_SECONDS,
            'processed': False,
            'cancelled': False
        }
    except Exception as e:
        print(f'Parse error: {e}')
    return None

def load_queue():
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE) as f:
            return json.load(f)
    return []

def save_queue(queue):
    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue, f, indent=2)

def log_message(text, msg_type, msg_time):
    log = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            log = json.load(f)
    log.append({
        'time': msg_time,
        'type': msg_type,
        'text': text[:200]
    })
    log = log[-1000:]
    with open(LOG_FILE, 'w') as f:
        json.dump(log, f, indent=2)

client = TelegramClient(
    '/root/gold-signals/gold_session',
    api_id, api_hash
)

@client.on(events.NewMessage(chats=channel_id))
async def handler(event):
    text = event.message.text
    # Skip messages before last known ID
    try:
        import json as _json
        last = _json.load(open(LAST_ID_FILE))
        if event.message.id <= last.get('id', 0):
            return
    except:
        pass
    if not text:
        return

    msg_time = event.message.date.isoformat()
    age_seconds = round((datetime.now(timezone.utc) - event.message.date).total_seconds())

    # Save last seen message ID
    try:
        import json as _json
        _json.dump({'id': event.message.id}, open(LAST_ID_FILE,'w'))
    except:
        pass
    msg_type = classify_message(text)
    log_message(text, msg_type, msg_time)
    print(f'[{msg_type.upper()}] age={age_seconds}s {text[:60]}')

    if msg_type == 'cancel':
        queue = load_queue()
        for s in reversed(queue):
            if not s.get('processed') and s.get('type') in ['buy','sell','buy_limit','sell_limit']:
                s['cancelled'] = True
                s['processed'] = True
                print(f'Cancelled: {s.get("zone_bot")}-{s.get("zone_top")}')
                break
        save_queue(queue)
        with open(CANCEL_FILE, 'w') as f:
            json.dump({
                'cancel': True,
                'time': msg_time,
                'text': text[:100]
            }, f)
        return

    if msg_type == 'ignore':
        return

    if msg_type == 'prepare':
        queue = load_queue()
        queue.append({
            'type': 'prepare',
            'time': msg_time,
            'age_seconds': age_seconds,
            'text': text,
            'processed': False
        })
        save_queue(queue)
        return

    if msg_type == 'exit_all':
        queue = load_queue()
        queue.append({
            'type': 'exit_all',
            'time': msg_time,
            'text': text,
            'processed': False
        })
        save_queue(queue)
        return

    if msg_type == 'sl_manage':
        import re as _re
        sl_match = _re.search(r'(\d{4}\.?\d*)', text)
        if sl_match:
            queue = load_queue()
            queue.append({
                'type': 'sl_manage',
                'new_sl': float(sl_match.group(1)),
                'time': msg_time,
                'processed': False
            })
            save_queue(queue)
        return

    signal = parse_signal(text, msg_type, msg_time, age_seconds)
    if not signal:
        return

    if signal['stale']:
        print(f'STALE signal ({age_seconds}s old) — queuing but flagged')

    queue = load_queue()
    if is_duplicate(signal):
        print(f"Buffer: duplicate rejected")
        return
    queue.append(signal)
    queue = queue[-100:]
    save_queue(queue)
    print(f'Signal queued: {signal["direction"]} {signal["zone_bot"]}-{signal["zone_top"]} age={age_seconds}s stale={signal["stale"]}')

STARTUP_TIME = datetime.now(timezone.utc)

async def main():
    await client.start()
    print('Telegram listener v2 started — watching AJD Trades V2')
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
