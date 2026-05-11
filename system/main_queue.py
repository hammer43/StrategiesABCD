#Strategy A Queue Version — reads from signal_queue.json
#Same trade logic as main.py but reads pipeline queue

import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/root/gold-signals')
from credentials import BOT_TOKEN, CHAT_ID
import urllib.request

BASE_DIR    = '/root/gold-signals/system'
QUEUE_FILE  = '/root/gold-signals/pipeline/signal_queue.json'
STATE_FILE  = f'{BASE_DIR}/state_aq.json'
TRADES_FILE = f'{BASE_DIR}/trades_aq.json'
MODEL_FILE  = f'{BASE_DIR}/model.pkl'

LABEL            = 'AQ'
STARTING_BALANCE = 5000.0
LOT_SIZE         = 0.05
PIP_VALUE        = LOT_SIZE * 10
ML_THRESHOLD     = 40
ENTRY_BUFFER     = 0.15
SL_BUFFER        = 0.2
TP1_BUFFER       = 0.3
MIN_RR           = 0.35
SKIP_HOURS       = {16, 22, 23, 0, 1, 2, 3, 4}

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

def send_telegram(message):
    try:
        msg = urllib.request.quote(str(message))
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={CHAT_ID}&text={msg}'
        urllib.request.urlopen(url, timeout=5)
    except Exception as e:
        print(f'Telegram error: {e}')

def load_ml_scorer():
    try:
        from ml_scorer import score_signal
        return score_signal
    except:
        return None

score_signal = load_ml_scorer()

def get_queue():
    try:
        if os.path.exists(QUEUE_FILE):
            with open(QUEUE_FILE) as f:
                q = json.load(f)
            now = datetime.now(timezone.utc)
            return [s for s in q if (now - datetime.fromisoformat(
                s['time'].replace('Z','+00:00'))).total_seconds() < 1800]
    except:
        pass
    return []

def is_good_session():
    return datetime.now(timezone.utc).hour not in SKIP_HOURS

def is_duplicate(tp1):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    for t in load_trades():
        if t.get('tp1') == tp1 and t.get('time_entry','') > cutoff:
            return True
    return False

account     = load_state()
open_trades = [t for t in load_trades() if t.get('status') == 'open']
processed_times = set()

def open_trade(signal):
    entry   = signal['entry']
    adj_sl  = signal['adj_sl']
    adj_tp1 = signal['adj_tp1']

    if is_duplicate(adj_tp1):
        return

    sl_pips  = round((entry - adj_sl) * 10, 1)
    tp1_pips = round((adj_tp1 - entry) * 10, 1)
    rr       = round(tp1_pips / sl_pips, 2) if sl_pips > 0 else 0
    tid      = f'AQ{account["total_trades"]+1:04d}'

    trade = {
        'id': tid, 'entry': entry,
        'sl': adj_sl, 'tp1': adj_tp1,
        'tp2': signal.get('tp2'), 'tp3': signal.get('tp3'),
        'sl_pips': sl_pips, 'tp1_pips': tp1_pips, 'rr': rr,
        'lots': LOT_SIZE, 'status': 'open',
        'tp1_hit': False, 'pnl': 0.0,
        'time_entry': datetime.now(timezone.utc).isoformat(),
        'time_exit': None
    }
    open_trades.append(trade)
    save_trade(trade)
    account['total_trades'] += 1
    save_state()
    send_telegram(
        f'[{LABEL}] TRADE {tid}\n'
        f'Entry: {entry}\n'
        f'SL: {adj_sl} ({sl_pips}p)\n'
        f'TP1: {adj_tp1} ({tp1_pips}p)\n'
        f'RR: {rr}\n'
        f'Balance: ${round(account["balance"],2)}'
    )

async def signal_processor():
    while True:
        try:
            queue = get_queue()
            for signal in queue:
                sig_time = signal.get('time','')
                if sig_time in processed_times:
                    continue
                if signal.get('type') not in ['buy','buy_limit']:
                    processed_times.add(sig_time)
                    continue

                # Build adjusted signal
                zone_top = signal.get('zone_top', 0)
                zone_bot = signal.get('zone_bot', 0)
                tp1      = signal.get('tp1', 0)
                tp2      = signal.get('tp2')
                tp3      = signal.get('tp3')
                sl       = signal.get('sl', 0)

                entry   = round(zone_top + ENTRY_BUFFER, 2)
                adj_tp1 = round(tp1 - TP1_BUFFER, 2)
                adj_sl  = round(sl - SL_BUFFER, 2)

                # Session filter
                if not is_good_session():
                    processed_times.add(sig_time)
                    continue

                # ML filter
                if score_signal:
                    result = score_signal(signal)
                    conf = result['confidence']
                    threshold = 50 if signal.get('out_of_office') else ML_THRESHOLD
                    if conf < threshold:
                        send_telegram(f'[{LABEL}] SKIP {round(conf,1)}%\nZone: {zone_bot}-{zone_top}')
                        processed_times.add(sig_time)
                        continue

                # RR filter
                tp1_pips = round((adj_tp1 - entry) * 10, 1)
                sl_pips  = round((entry - adj_sl) * 10, 1)
                rr = round(tp1_pips / sl_pips, 2) if sl_pips > 0 else 0
                if rr < MIN_RR:
                    send_telegram(f'[{LABEL}] SKIP low RR {rr}\nZone: {zone_bot}-{zone_top}')
                    processed_times.add(sig_time)
                    continue

                # Set pending
                if account.get('pending'):
                    processed_times.add(sig_time)
                    continue

                signal['entry']   = entry
                signal['adj_tp1'] = adj_tp1
                signal['adj_sl']  = adj_sl
                account['pending'] = signal
                save_state()
                processed_times.add(sig_time)
                send_telegram(
                    f'[{LABEL}] SIGNAL {round(conf if score_signal else 100, 1)}%\n'
                    f'Zone: {zone_bot}-{zone_top}\n'
                    f'Entry: {entry} TP1: {adj_tp1} SL: {adj_sl}\n'
                    f'RR: {rr}'
                )

        except Exception as e:
            print(f'Signal processor error: {e}')
        await asyncio.sleep(5)

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

            if account.get('pending'):
                p     = account['pending']
                entry = p['entry']

                if price >= p['tp1']:
                    print(f'SILENT CANCEL: TP1 already hit price={price}')
                    account['pending'] = None
                    save_state()
                elif price <= entry + 0.15:
                    open_trade(p)
                    account['pending'] = None
                    save_state()
                elif prev_price is not None and prev_price < entry and price >= entry:
                    if price < p['adj_tp1'] and price > p['adj_sl']:
                        open_trade(p)
                        account['pending'] = None
                        save_state()

            for trade in open_trades:
                if trade['status'] != 'open':
                    continue
                if price <= trade['sl']:
                    loss = round(trade['sl_pips'] * PIP_VALUE, 2)
                    account['balance']      -= loss
                    account['total_losses'] += 1
                    trade['status']    = 'loss'
                    trade['pnl']       = -loss
                    trade['exit']      = price
                    trade['time_exit'] = datetime.now(timezone.utc).isoformat()
                    save_trade(trade)
                    save_state()
                    wr = round(account['total_wins']/(account['total_wins']+account['total_losses'])*100,1) if (account['total_wins']+account['total_losses']) > 0 else 0
                    send_telegram(
                        f'[{LABEL}] STOP LOSS {trade["id"]}\n'
                        f'Entry:{trade["entry"]} Exit:{price}\n'
                        f'PnL: -${loss}\n'
                        f'Balance: ${round(account["balance"],2)}\n'
                        f'WR: {wr}%'
                    )
                elif price >= trade['tp1'] and not trade['tp1_hit']:
                    pips   = round((trade['tp1']-trade['entry'])*10,1)
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
                        f'[{LABEL}] TP1 {trade["id"]}\n'
                        f'+{pips}p +${profit}\n'
                        f'Balance: ${round(account["balance"],2)}'
                    )

        except Exception as e:
            print(f'Price monitor error: {e}')

        prev_price = price
        await asyncio.sleep(1)

async def daily_report():
    from datetime import timedelta
    while True:
        now    = datetime.now(timezone.utc)
        target = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target-now).total_seconds())
        trades  = load_trades()
        today   = datetime.now(timezone.utc).date().isoformat()
        t_today = [t for t in trades if t.get('time_entry','').startswith(today)]
        wins    = [t for t in t_today if t.get('tp1_hit')]
        losses  = [t for t in t_today if t.get('status')=='loss']
        pnl     = sum(t.get('pnl',0) for t in t_today)
        ret     = round((account['balance']-STARTING_BALANCE)/STARTING_BALANCE*100,2)
        wr      = round(len(wins)/(len(wins)+len(losses))*100,1) if (wins or losses) else 0
        send_telegram(
            f'[{LABEL}] DAILY {today}\n'
            f'Trades: {len(t_today)} W:{len(wins)} L:{len(losses)}\n'
            f'WR: {wr}% PnL: ${round(pnl,2)}\n'
            f'Balance: ${round(account["balance"],2)}\n'
            f'Return: {ret}%'
        )

async def main():
    send_telegram(
        f'[{LABEL}] STRATEGY AQ LIVE (Queue version)\n'
        f'Entry: zone_top + {ENTRY_BUFFER}\n'
        f'TP1: signal_tp1 - {TP1_BUFFER} (100% close)\n'
        f'SL: signal_sl - {SL_BUFFER}\n'
        f'ML: {ML_THRESHOLD}% RR: {MIN_RR}\n'
        f'Balance: ${round(account["balance"],2)}'
    )
    await asyncio.gather(
        signal_processor(),
        price_monitor(),
        daily_report()
    )

asyncio.run(main())