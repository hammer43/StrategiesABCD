import asyncio
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, events

# ── CREDENTIALS ──────────────────────────────────────────
sys.path.insert(0, '/root/gold-signals')
from credentials import API_ID, API_HASH, BOT_TOKEN, CHAT_ID, CHANNEL_ID

# ── PATHS ─────────────────────────────────────────────────
BASE_DIR    = '/root/gold-signals/system'
STATE_FILE  = f'{BASE_DIR}/state.json'
TRADES_FILE = f'{BASE_DIR}/trades.json'
SESSION     = f'{BASE_DIR}/gold_session_a'
MODEL_FILE  = f'{BASE_DIR}/model.pkl'

# ── CONSTANTS ─────────────────────────────────────────────
LABEL            = 'A'
STARTING_BALANCE = 5000.0
LOT_SIZE         = 0.05
PIP_VALUE        = LOT_SIZE * 10        # $0.50 per pip
ML_THRESHOLD     = 40                   # % minimum confidence
ENTRY_BUFFER     = 0.15                 # zone_top + 0.15
SL_BUFFER        = 0.2                  # signal_sl - 0.2
TP1_BUFFER       = 0.3                  # signal_tp1 - 0.3
REPORT_HOUR_UTC  = 12                   # Daily report time

# ── SKIP HOURS (UTC) ─────────────────────────────────────
SKIP_HOURS = {16, 22, 23, 0, 1, 2, 3, 4}

# ── IGNORE KEYWORDS ──────────────────────────────────────
IGNORE_KEYWORDS = [
    'CLOSE +', 'SECURING',
    'PARTIAL', 'TP1 HIT', 'TP2 HIT', 'TP3 HIT',
    'SL HIT', 'BREAKEVEN', 'LOCKED IN',
    'RECAP', 'TRADERS', 'PERFECT', 'INSANE',
    'PREPARING', 'WELL DONE', 'WHO HELD',
]

CANCEL_KEYWORDS = ['CANCEL', 'CANCELLED', 'DISREGARD', 'IGNORE LAST']

# ── STATE ─────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            pass
    return {
        'balance': STARTING_BALANCE,
        'starting': STARTING_BALANCE,
        'pending': None,
        'total_trades': 0,
        'total_wins': 0,
        'total_losses': 0,
        'total_breakeven': 0
    }

def save_state():
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(account, f, indent=2)
    os.replace(tmp, STATE_FILE)

# ── TRADES ────────────────────────────────────────────────
def load_trades():
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE) as f:
                return json.load(f)
        except:
            pass
    return []

def save_trade(trade):
    trades = load_trades()
    trades = [t for t in trades if t['id'] != trade['id']]
    trades.append(trade)
    tmp = TRADES_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(trades, f, indent=2)
    os.replace(tmp, TRADES_FILE)

# ── TELEGRAM ──────────────────────────────────────────────
def send_telegram(message):
    try:
        msg = urllib.request.quote(str(message))
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={CHAT_ID}&text={msg}'
        urllib.request.urlopen(url, timeout=5)
    except Exception as e:
        print(f'Telegram error: {e}')

# ── ML SCORER ─────────────────────────────────────────────
def load_ml_scorer():
    try:
        sys.path.insert(0, BASE_DIR)
        from ml_scorer import score_signal
        return score_signal
    except Exception as e:
        print(f'ML scorer error: {e}')
        return None

score_signal = load_ml_scorer()

# ── SIGNAL PARSER ─────────────────────────────────────────
def parse_signal(text):
    try:
        zone  = re.search(r'(\d{4}\.?\d*)/(\d{4}\.?\d*)', text)
        tps   = re.findall(r'TP\s+(\d{4}\.?\d*)', text)
        sl    = re.search(r'SL\s+(\d{4}\.?\d*)', text)
        if not (zone and tps and sl):
            return None
        zt       = float(zone.group(1))
        zb       = float(zone.group(2))
        zone_top = max(zt, zb)
        zone_bot = min(zt, zb)
        tp_list  = [float(t) for t in tps]
        sl_val   = float(sl.group(1))
        upper    = text.upper()
        return {
            'zone_top':  zone_top,
            'zone_bot':  zone_bot,
            'tp1':       tp_list[0],
            'tp2':       tp_list[1] if len(tp_list) > 1 else None,
            'tp3':       tp_list[2] if len(tp_list) > 2 else None,
            'sl':        sl_val,
            'high_risk': 'HIGH RISK' in upper,
            'entry':     round(zone_top + ENTRY_BUFFER, 2),
            'adj_sl':    round(sl_val - SL_BUFFER, 2),
            'adj_tp1':   round(tp_list[0] - TP1_BUFFER, 2),
        }
    except Exception as e:
        print(f'Parse error: {e}')
        return None

# ── SESSION FILTER ────────────────────────────────────────
def is_good_session():
    hour = datetime.now(timezone.utc).hour
    return hour not in SKIP_HOURS

# ── TRADE MANAGEMENT ──────────────────────────────────────
account     = load_state()
open_trades = [t for t in load_trades() if t.get('status') == 'open']

def is_duplicate_trade(tp1):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    for t in load_trades():
        if t.get('tp1') == tp1 and t.get('time_entry', '') > cutoff:
            return True
    return False

def open_trade(signal):
    entry   = signal['entry']
    adj_sl  = signal['adj_sl']
    adj_tp1 = signal['adj_tp1']

    # Duplicate check
    if is_duplicate_trade(adj_tp1):
        print(f'Duplicate prevented: tp1={adj_tp1}')
        return

    sl_pips  = round((entry - adj_sl) * 10, 1)
    tp1_pips = round((adj_tp1 - entry) * 10, 1)
    rr       = round(tp1_pips / sl_pips, 2) if sl_pips > 0 else 0
    tid      = f'A{account["total_trades"]+1:04d}'

    trade = {
        'id':          tid,
        'entry':       entry,
        'sl':          adj_sl,
        'tp1':         adj_tp1,
        'tp2':         signal.get('tp2'),
        'tp3':         signal.get('tp3'),
        'sl_pips':     sl_pips,
        'tp1_pips':    tp1_pips,
        'rr':          rr,
        'lots':        LOT_SIZE,
        'status':      'open',
        'tp1_hit':     False,
        'pnl':         0.0,
        'time_entry':  datetime.now(timezone.utc).isoformat(),
        'time_exit':   None,
        'high_risk':   signal.get('high_risk', False)
    }
    open_trades.append(trade)
    save_trade(trade)
    account['total_trades'] += 1
    save_state()

    send_telegram(
        f'[{LABEL}] TRADE {tid}\n'
        f'Entry: {entry}\n'
        f'SL: {adj_sl} ({sl_pips} pips)\n'
        f'TP1: {adj_tp1} ({tp1_pips} pips)\n'
        f'RR: {rr}\n'
        f'Balance: ${round(account["balance"], 2)}'
    )
    print(f'Trade opened: {tid} entry={entry} tp1={adj_tp1}')

# ── PRICE MONITOR ─────────────────────────────────────────
async def price_monitor():
    from price_engine import PriceEngine
    engine = PriceEngine()
    engine.start()
    await asyncio.sleep(3)
    prev_price = None

    while True:
        try:
            price = engine.current_price
            if not price:
                await asyncio.sleep(1)
                continue

            # Check pending fill
            if account.get('pending'):
                p     = account['pending']
                entry = p['entry']

                # Cancel if TP1 already hit
                if price >= p['adj_tp1']:
                    send_telegram(
                        f'[{LABEL}] CANCELLED\n'
                        f'TP1 {p["adj_tp1"]} already hit\n'
                        f'Price: {price}'
                    )
                    account['pending'] = None
                    save_state()

                # Fill if price at entry (drop to zone)
                elif price <= entry + 0.15:
                    open_trade(p)
                    account['pending'] = None
                    save_state()
                # Fill if price crosses entry going up
                elif prev_price is not None and prev_price < entry and price >= entry:
                    if price < p['adj_tp1'] and price > p['adj_sl']:
                        open_trade(p)
                        account['pending'] = None
                        save_state()

            # Monitor open trades
            for trade in open_trades:
                if trade['status'] != 'open':
                    continue

                # SL hit
                if price <= trade['sl']:
                    lots = trade.get('lots', LOT_SIZE)
                    loss = round((trade['entry'] - price) * lots * 100, 2)
                    loss = max(loss, 0)
                    account['balance']      -= loss
                    account['total_losses'] += 1
                    trade['status']    = 'loss'
                    trade['pnl']       = -loss
                    trade['exit']      = price
                    trade['time_exit'] = datetime.now(timezone.utc).isoformat()
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'[{LABEL}] STOP LOSS - {trade["id"]}\n'
                        f'Entry: {trade["entry"]} Exit: {price}\n'
                        f'PnL: -${loss}\n'
                        f'Balance: ${round(account["balance"], 2)}\n'
                        f'WR: {round(account["total_wins"]/(account["total_wins"]+account["total_losses"])*100, 1)}%'
                    )

                # TP1 hit — close 100%
                elif price >= trade['tp1'] and not trade['tp1_hit']:
                    pips   = round((trade['tp1'] - trade['entry']) * 10, 1)
                    profit = round(pips * PIP_VALUE, 2)
                    account['balance']    += profit
                    account['total_wins'] += 1
                    trade['tp1_hit']   = True
                    trade['status']    = 'closed'
                    trade['pnl']       = profit
                    trade['exit']      = price
                    trade['time_exit'] = datetime.now(timezone.utc).isoformat()
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'[{LABEL}] TP1 - {trade["id"]}\n'
                        f'+{pips} pips | +${profit}\n'
                        f'Balance: ${round(account["balance"], 2)}'
                    )

        except Exception as e:
            print(f'Price monitor error: {e}')

        prev_price = price
        await asyncio.sleep(1)

# ── DAILY REPORT ──────────────────────────────────────────
async def daily_report():
    while True:
        now    = datetime.now(timezone.utc)
        target = now.replace(hour=REPORT_HOUR_UTC, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        trades = load_trades()
        today  = datetime.now(timezone.utc).date().isoformat()
        t_today = [t for t in trades if t.get('time_entry','').startswith(today)]
        wins    = [t for t in t_today if t.get('tp1_hit')]
        losses  = [t for t in t_today if t.get('status') == 'loss']
        pnl     = sum(t.get('pnl', 0) for t in t_today)
        ret     = round((account['balance'] - STARTING_BALANCE) / STARTING_BALANCE * 100, 2)
        wr      = round(len(wins)/(len(wins)+len(losses))*100, 1) if (wins or losses) else 0

        send_telegram(
            f'DAILY REPORT - {today}\n'
            f'Trades: {len(t_today)}\n'
            f'Winners: {len(wins)}\n'
            f'Losers: {len(losses)}\n'
            f'WR: {wr}%\n'
            f'PnL: {round(pnl, 2)}\n'
            f'Balance: {round(account["balance"], 2)}\n'
            f'Return: {ret}%\n'
            f'W/BE/L: {account["total_wins"]}/{account["total_breakeven"]}/{account["total_losses"]}'
        )

# ── TELEGRAM HANDLER ──────────────────────────────────────
STARTUP_TIME          = datetime.now(timezone.utc)
processed_message_ids = set()

client = TelegramClient(SESSION, API_ID, API_HASH)

@client.on(events.NewMessage(chats=CHANNEL_ID))
async def handler(event):
    # Message ID dedup
    if event.message.id in processed_message_ids:
        return
    processed_message_ids.add(event.message.id)

    # Startup filter
    if event.message.date.astimezone(timezone.utc) < STARTUP_TIME:
        return

    text = event.message.text
    if not text:
        return
    upper = text.upper()

    # Cancel handler
    if any(kw in upper for kw in CANCEL_KEYWORDS) and len(text) < 60:
        if account.get('pending'):
            account['pending'] = None
            save_state()
            send_telegram(f'[{LABEL}] CANCELLED by signaller')
        return

    # SL management messages
    sl_mgmt_keywords = ['RISK FREE', 'MOVE SL', 'SL TO BE', 'SL TO']
    if any(kw in upper for kw in sl_mgmt_keywords):
        # Extract new SL level
        sl_match = re.search(r'(RISK FREE|MOVE SL|SL TO BE?)\s*[\@\-\s]?\s*(\d{4}\.?\d*)', upper)
        if sl_match and open_trades:
            new_sl = float(sl_match.group(2))
            for trade in open_trades:
                if trade['status'] == 'open' and new_sl > trade['sl']:
                    trade['sl'] = new_sl
                    save_trade(trade)
                    print(f'SL moved to {new_sl} for {trade["id"]}')
            send_telegram(f'[{LABEL}] SL managed → {new_sl}')
        return

    # Ignore management messages
    if any(kw in upper for kw in IGNORE_KEYWORDS):
        return

    # Only process BUY signals
    if not (('BUY GOLD' in upper or 'BUY XAUUSD' in upper or 'BUY LIMITS' in upper) and 'SL' in upper and 'TP' in upper):
        return

    # Parse signal
    signal = parse_signal(text)
    if not signal:
        return

    # ML score
    if score_signal:
        result = score_signal(signal)
        conf   = result['confidence']
        threshold = 50 if signal.get("out_of_office") else ML_THRESHOLD
        if conf < threshold:
            send_telegram(
                f'[{LABEL}] SKIP {conf}%\n'
                f'Zone: {signal["zone_bot"]}-{signal["zone_top"]}'
            )
            return
    else:
        conf = 100

    # Session filter
    if not is_good_session():
        send_telegram(
            f'[{LABEL}] SKIP bad hour\n'
            f'Zone: {signal["zone_bot"]}-{signal["zone_top"]}'
        )
        return

    # Check if pending exists
    if account.get('pending'):
        old = account['pending']
        send_telegram(
            f'[{LABEL}] Pending active\n'
            f'Old zone: {old["zone_bot"]}-{old["zone_top"]}\n'
            f'New zone: {signal["zone_bot"]}-{signal["zone_top"]}'
        )
        return

    # Minimum RR filter
    entry = round(signal["zone_top"] + ENTRY_BUFFER, 2)
    tp1_pips = round((signal["adj_tp1"] - entry) * 10, 1)
    sl_pips = round((entry - signal["adj_sl"]) * 10, 1)
    rr = round(tp1_pips / sl_pips, 2) if sl_pips > 0 else 0
    if rr < 0.35:
        send_telegram(f'[{LABEL}] SKIP low RR {rr}\nZone: {signal["zone_bot"]}-{signal["zone_top"]}')
        return
    # Set pending
    account['pending'] = signal
    save_state()
    send_telegram(
        f'[{LABEL}] SIGNAL {round(conf, 1)}%\n'
        f'Zone: {signal["zone_bot"]}-{signal["zone_top"]}\n'
        f'Entry: {signal["entry"]}\n'
        f'TP1: {signal["adj_tp1"]}\n'
        f'SL: {signal["adj_sl"]}'
    )

# ── MAIN ──────────────────────────────────────────────────
async def main():
    await client.start()
    send_telegram(
        f'[{LABEL}] STRATEGY A LIVE\n'
        f'Entry: zone_top + {ENTRY_BUFFER}\n'
        f'TP1: signal_tp1 - {TP1_BUFFER} (100% close)\n'
        f'SL: signal_sl - {SL_BUFFER}\n'
        f'ML threshold: {ML_THRESHOLD}%\n'
        f'Balance: ${round(account["balance"], 2)}\n'
        f'Trades: {account["total_trades"]}'
    )
    await asyncio.gather(
        price_monitor(),
        daily_report(),
        client.run_until_disconnected()
    )

asyncio.run(main())