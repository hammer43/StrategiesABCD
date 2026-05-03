import asyncio
import json
import re
import os
import pandas as pd
import urllib.request
from datetime import datetime, timezone, timedelta
from ml.hybrid_scorer import hybrid_score
from candle_daemon import load_candles, get_htf_bias
from session_filter import get_session_info

# ── CREDENTIALS ──────────────────────────────────────────
api_id      = 29091418
api_hash    = '3d1d32748831ac3d51991d24d03623bd'
channel_id  = -1003293033145
bot_token   = '8680392041:AAFKpVrYzWHQrR-4_-BbmA-eWIC6BV4Zp8s'
my_chat_id  = 1273237796
LABEL       = 'C'

# ── CONFIG ───────────────────────────────────────────────
RISK_TOTAL       = 50.0
LOT_SIZE         = 0.05
PIP_VALUE        = LOT_SIZE * 10
BREAKEVEN_BUFFER = 0.2
FILL_BUFFER      = 0.15
STATE_FILE       = '/root/gold-signals/pipeline/state_c.json'
PROCESSED_FILE = '/root/gold-signals/pipeline/processed_c.json'
TRADES_FILE      = '/root/gold-signals/pipeline/trades_c.json'
QUEUE_FILE       = '/root/gold-signals/pipeline/signal_queue.json'
PRICE_FILE       = '/root/gold-signals/pipeline/price.json'
CANDLE_DIR       = '/root/gold-signals/pipeline/candles'

# ── IGNORE KEYWORDS ──────────────────────────────────────
IGNORE_KEYWORDS = [
    'RISK FREE', 'SL TO BE', 'CLOSE +', 'SECURING',
    'PARTIAL', 'TP1 HIT', 'TP2 HIT', 'TP3 HIT',
    'SL HIT', 'BREAKEVEN', 'MOVE SL', 'LOCKED IN',
    'TRAILING', 'OUT OF OFFICE', 'NOT AROUND',
    'AT YOUR OWN RISK', 'PIPS'
]

# ── STATE ─────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
            print(f'[C] Resumed: balance=${s["balance"]} trades={s["total_trades"]}')
            return s
    return {
        'balance': 5000.0, 'starting': 5000.0,
        'pending': None,
        'total_trades': 0, 'total_wins': 0,
        'total_losses': 0, 'total_breakeven': 0,
        'recent_zones': []
    }

def save_state():
    import tempfile, os
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(account, f, indent=2)
    os.replace(tmp, STATE_FILE)

def load_trades():
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f:
            return json.load(f)
    return []

def save_trade(trade):
    trades = load_trades()
    trades = [t for t in trades if t['id'] != trade['id']]
    trades.append(trade)
    with open(TRADES_FILE, 'w') as f:
        json.dump(trades, f, indent=2)

account     = load_state()
open_trades = [t for t in load_trades() if t.get('status') == 'open']

# ── HELPERS ──────────────────────────────────────────────
def send_telegram(message):
    try:
        msg = urllib.request.quote(str(message))
        url = (f'https://api.telegram.org/bot{bot_token}'
               f'/sendMessage?chat_id={my_chat_id}&text={msg}')
        urllib.request.urlopen(url, timeout=5)
    except Exception as e:
        print(f'Telegram error: {e}')

def get_price():
    try:
        if os.path.exists(PRICE_FILE):
            with open(PRICE_FILE) as f:
                return json.load(f)
    except:
        pass
    return None

def get_queue():
    try:
        if os.path.exists(QUEUE_FILE):
            with open(QUEUE_FILE) as f:
                q = json.load(f)
            now = datetime.now(timezone.utc)
            return [s for s in q if (now - datetime.fromisoformat(s['time'].replace('Z','+00:00'))).total_seconds() < 600]
    except:
        pass
    return []

def mark_processed(signal_time):
    return  # Each strategy tracks independently
    queue = get_queue()
    for s in queue:
        if s.get('time') == signal_time:
            s['processed'] = True
    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue, f, indent=2)

def to_df(candles):
    if not candles:
        return None
    df = pd.DataFrame(candles)
    df['datetime'] = pd.to_datetime(df['datetime'])
    for col in ['open','high','low','close']:
        df[col] = df[col].astype(float)
    return df.sort_values('datetime').reset_index(drop=True)

def get_candles():
    return {
        '1m':  to_df(load_candles('1m')),
        '5m':  to_df(load_candles('5m')),
        '15m': to_df(load_candles('15m')),
        '4h':  to_df(load_candles('4h'))
    }

def detect_sweep(df):
    if df is None or len(df) < 20:
        return None
    last = df.iloc[-1]
    prev_low = df['low'].rolling(20).min().iloc[-10]
    if last['low'] < prev_low and last['close'] > last['open']:
        cr = last['high'] - last['low']
        if cr == 0:
            return None
        return {
            'depth': round((prev_low-last['low'])*10, 1),
            'wick_ratio': round((last['open']-last['low'])/cr, 2),
            'body_ratio': round((last['close']-last['open'])/cr, 2),
            'sweep_strength': round((prev_low-last['low'])*10*(last['open']-last['low'])/cr, 2)
        }
    return None

def detect_fvg(df):
    if df is None or len(df) < 3:
        return None
    for i in range(len(df)-1, 1, -1):
        c1 = df.iloc[i-2]
        c3 = df.iloc[i]
        if c1['high'] < c3['low']:
            top = c3['low']
            bot = c1['high']
            mid = (top+bot)/2
            return {
                'top': top, 'bottom': bot, 'mid': mid,
                'bullish': True, 'age': len(df)-i,
                'price_to_50pct': 0
            }
    return None

def is_reentry(signal, recent_zones):
    for zone in recent_zones[-10:]:
        if abs(zone - signal['zone_top']) < 5:
            return True
    return False

def get_size_multiplier(signal, htf_bias, sweep, is_re, session):
    mult = session['size']

    # Optimised C rules:
    # Bearish bias → half size (not skip)
    if htf_bias == 'bearish':
        mult *= 0.5

    # Re-entry → size up 1.5x
    if is_re:
        mult *= 1.5

    # Out of office → half size
    if signal.get('out_of_office'):
        mult *= 0.5

    return round(min(mult, 2.0), 2)

def get_entries(signal, score, size_mult):
    zone_top = signal['zone_top']
    zone_70  = signal['zone_70pct']
    tier     = score['tier']
    base     = RISK_TOTAL * size_mult

    if tier == 'high':
        return [
            {'price': round(zone_top - 0.1, 2), 'risk': base/2, 'label': 'L1'},
            {'price': zone_top - 1.0, 'risk': base/2, 'label': 'L2'}
        ]
    elif tier == 'mid':
        return [
            {'price': round(zone_top - 0.1, 2), 'risk': base/2, 'label': 'L1'},
            {'price': zone_70,  'risk': base/2, 'label': 'L2'}
        ]
    else:
        return [
            {'price': round(zone_top - 0.1, 2), 'risk': base, 'label': 'L1'}
        ]

def open_trade(entry_info, signal, trade_type):
    entry = entry_info['price']
    risk  = entry_info['risk']
    label = entry_info['label']
    # Duplicate check
    for ex in open_trades:
        if ex.get('status') == 'open' and abs(ex.get('entry',0) - entry) < 0.5 and ex.get('tp1') == signal.get('tp1'):
            print(f'Duplicate prevented: {entry}')
            return
    tid   = f"C{account['total_trades']+1:04d}{label}"

    price_dist = abs(entry - signal['sl'])
    lots = round(risk / (price_dist * 100), 2) if price_dist > 0 else 0.01
    lots = max(0.01, min(lots, 2.0))

    sl_pips  = round((entry - signal['sl']) * 10, 1)
    tp1_pips = round((signal['tp1'] - entry) * 10, 1)
    rr = round(tp1_pips/sl_pips, 2) if sl_pips > 0 else 0

    trade = {
        'id': tid, 'strategy': 'C',
        'type': trade_type, 'layer': label,
        'direction': signal.get('direction', 'BUY'),
        'entry': entry, 'sl': signal['sl'],
        'sl_original': signal['sl'],
        'tp1': signal['tp1'], 'tp2': signal['tp2'], 'tp3': signal['tp3'],
        'lots': lots, 'risk': risk,
        'sl_pips': sl_pips, 'tp1_pips': tp1_pips, 'rr': rr,
        'status': 'open',
        'tp1_hit': False, 'tp2_hit': False, 'tp3_hit': False,
        'pnl': 0.0,
        'time_entry': datetime.now(timezone.utc).isoformat(),
        'time_exit': None
    }
    open_trades.append(trade)
    save_trade(trade)
    account['total_trades'] += 1
    save_state()

    send_telegram(
        f'[{LABEL}] TRADE {label} - {tid}\n'
        f'Type: {trade_type}\n'
        f'Entry: {entry}\n'
        f'SL: {signal["sl"]} ({sl_pips}pips)\n'
        f'TP1: {signal["tp1"]} ({tp1_pips}pips)\n'
        f'RR: {rr}\n'
        f'Risk: ${round(risk,2)}\n'
        f'Balance: ${round(account["balance"],2)}'
    )

# ── SIGNAL PROCESSOR ─────────────────────────────────────
async def signal_processor():
    # Load persistent processed times
    processed_times = set()
    if os.path.exists(PROCESSED_FILE):
        try:
            processed_times = set(json.load(open(PROCESSED_FILE)))
        except:
            pass

    def save_processed():
        tmp = PROCESSED_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(list(processed_times)[-500:], f)
        os.replace(tmp, PROCESSED_FILE)

    while True:
        try:
            queue  = get_queue()
            pdata  = get_price()
            price  = pdata['price'] if pdata else None
            direction = pdata['direction'] if pdata else 'unknown'

            for signal in queue:
                # Skip signals older than 30 minutes
                from datetime import datetime, timezone
                try:
                    sig_age = (datetime.now(timezone.utc) - datetime.fromisoformat(signal["time"])).total_seconds()
                    if sig_age > 1800:
                        processed_times.add(signal.get("time",""))
                        continue
                except:
                    pass
                if signal.get('processed'):
                    continue
                if signal.get('time') in processed_times:
                    continue
                if signal.get('type') == 'prepare':
                    processed_times.add(signal["time"])
                    save_processed()
                    continue
                if signal.get('direction') != 'BUY':
                    processed_times.add(signal["time"])
                    save_processed()
                    continue

                # Get market context from candles
                candles  = get_candles()
                sweep    = detect_sweep(candles['5m'])
                fvg      = detect_fvg(candles['5m'])
                htf_bias = get_htf_bias()
                mitigation = (fvg['bottom']+(fvg['top']-fvg['bottom'])*0.5) if fvg else None

                if fvg and mitigation and price:
                    fvg['price_to_50pct'] = abs(price - mitigation)

                # Check re-entry
                is_re = is_reentry(signal, account.get('recent_zones', []))

                # Hybrid ML score
                score = hybrid_score(
                    signal,
                    candles['1m'], candles['5m'],
                    candles['15m'], candles['4h'],
                    fvg, sweep, mitigation, htf_bias
                )

                # Strategy C: skip only if truly bad
                # Bearish = half size, not skip
                if score['confidence'] < 60 and htf_bias == 'bearish':
                    send_telegram(
                        f'[{LABEL}] SKIP {score["confidence"]}%\n'
                        f'Bearish + low confidence\n'
                        f'Zone: {signal["zone_bot"]}-{signal["zone_top"]}'
                    )
                    processed_times.add(signal["time"])
                    save_processed()
                    mark_processed(signal['time'])
                    continue

                if score['tier'] == 'skip' and htf_bias != 'bearish':
                    send_telegram(
                        f'[{LABEL}] SKIP {score["confidence"]}%\n'
                        f'Zone: {signal["zone_bot"]}-{signal["zone_top"]}'
                    )
                    processed_times.add(signal["time"])
                    save_processed()
                    mark_processed(signal['time'])
                    continue

                # Session filter
                session = get_session_info()

                # Size multiplier with C optimised rules
                size_mult = get_size_multiplier(signal, htf_bias, sweep, is_re, session)

                # Replace stale pending
                if account.get('pending'):
                    old = account['pending']
                    if price and abs(price - old['zone_top']) > 15:
                        send_telegram(f'[{LABEL}] Stale pending replaced')
                        account['pending'] = None
                    else:
                        processed_times.add(signal["time"])
                        save_processed()
                        mark_processed(signal['time'])
                        continue

                # Build entries
                entries = get_entries(signal, score, size_mult)

                # Entry direction
                if price and price > signal['fill_trigger']:
                    entry_dir = 'wait_drop'
                elif price and price < signal['zone_bot']:
                    entry_dir = 'wait_rise'
                else:
                    entry_dir = 'in_range'

                signal['entries']         = entries
                signal['entry_direction'] = entry_dir
                signal['score']           = score
                signal['htf_bias']        = htf_bias
                signal['sweep_detected']  = sweep is not None
                signal['is_reentry']      = is_re
                signal['size_mult']       = size_mult

                account['pending'] = signal

                # Track zone for re-entry detection
                zones = account.get('recent_zones', [])
                zones.append(signal['zone_top'])
                account['recent_zones'] = zones[-20:]
                save_state()

                reentry_flag = ' RE-ENTRY 1.5x' if is_re else ''
                bearish_flag = ' BEARISH-HALF' if htf_bias == 'bearish' else ''
                sweep_flag   = ' SWEEP' if sweep else ''

                send_telegram(
                    f'[{LABEL}] SIGNAL {score["tier"].upper()}\n'
                    f'Zone: {signal["zone_bot"]}-{signal["zone_top"]}\n'
                    f'TP1: {signal["tp1"]} SL: {signal["sl"]}\n'
                    f'Hybrid: {score["confidence"]}%\n'
                    f'Size: {size_mult}x{reentry_flag}{bearish_flag}{sweep_flag}\n'
                    f'HTF: {htf_bias} | FVG: {fvg is not None}\n'
                    f'Direction: {entry_dir}\n'
                    f'Entries: {[e["price"] for e in entries]}'
                )

                processed_times.add(signal["time"])
                save_processed()
                mark_processed(signal['time'])

        except Exception as e:
            print(f'[C] Signal processor error: {e}')

        await asyncio.sleep(1)

# ── PRICE MONITOR ─────────────────────────────────────────
async def price_monitor():
    filled_layers = set()

    while True:
        try:
            pdata     = get_price()
            if not pdata or not pdata.get('price'):
                await asyncio.sleep(1)
                continue

            price     = pdata['price']
            direction = pdata['direction']

            if account.get('pending'):
                p         = account['pending']
                entries   = p.get('entries', [])
                entry_dir = p.get('entry_direction', 'wait_drop')

                # TP1 already hit
                if price >= p['tp1']:
                    send_telegram(
                        f'[{LABEL}] CANCELLED\n'
                        f'TP1 {p["tp1"]} already hit\n'
                        f'Price: {price} trap avoided'
                    )
                    account['pending'] = None
                    filled_layers.clear()
                    save_state()

                else:
                    for entry_info in entries:
                        eid = f'{p["time"]}_{entry_info["label"]}'
                        if eid in filled_layers:
                            continue
                        target = entry_info['price']

                        if entry_dir == 'wait_drop':
                            if price < p['zone_bot'] - 0.1:
                                p['entry_direction'] = 'wait_rise'
                                for e in entries:
                                    e['price'] = p['zone_bot']
                                save_state()
                            elif price <= target + FILL_BUFFER:
                                open_trade(entry_info, p, 'limit_drop')
                                filled_layers.add(eid)

                        elif entry_dir == 'wait_rise':
                            if price >= target and direction == 'up':
                                open_trade(entry_info, p, 'bounce_rise')
                                filled_layers.add(eid)

                        elif entry_dir == 'in_range':
                            if price <= target + FILL_BUFFER:
                                open_trade(entry_info, p, 'zone_entry')
                                filled_layers.add(eid)

                    if entries and all(
                        f'{p["time"]}_{e["label"]}' in filled_layers
                        for e in entries
                    ):
                        account['pending'] = None
                        filled_layers.clear()
                        save_state()

            # Open trades
            for trade in open_trades:
                if trade['status'] != 'open':
                    continue

                if price <= trade['sl']:
                    is_be = trade.get('tp1_hit', False)
                    loss  = round(abs(trade['entry']-trade['sl'])*trade['lots']*100, 2)

                    if is_be:
                        account['total_breakeven'] += 1
                        trade['status'] = 'breakeven'
                        trade['pnl']    = 0.0
                    else:
                        account['balance']      -= loss
                        account['total_losses'] += 1
                        trade['status'] = 'loss'
                        trade['pnl']    = -loss

                    trade['exit']      = price
                    trade['time_exit'] = datetime.now(timezone.utc).isoformat()
                    save_trade(trade)
                    save_state()

                    total = account['total_wins'] + account['total_losses']
                    wr = round(account['total_wins']/total*100,1) if total > 0 else 0
                    status = 'BREAKEVEN' if is_be else 'STOP LOSS'
                    send_telegram(
                        f'[{LABEL}] {status} - {trade["id"]}\n'
                        f'Entry:{trade["entry"]} Exit:{price}\n'
                        f'PnL: ${round(trade["pnl"],2)}\n'
                        f'Balance: ${round(account["balance"],2)}\n'
                        f'WR: {wr}%'
                    )

                elif price >= trade['tp1'] and not trade['tp1_hit']:
                    pips   = round((trade['tp1']-trade['entry'])*10, 1)
                    profit = round(pips * PIP_VALUE * 0.33, 2)
                    account['balance']    += profit
                    account['total_wins'] += 1
                    trade['tp1_hit'] = True
                    trade['sl']      = round(trade['entry'] - BREAKEVEN_BUFFER, 2)
                    trade['pnl']    += profit
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'[{LABEL}] TP1 - {trade["id"]}\n'
                        f'+{pips}pips | +${profit}\n'
                        f'SL Breakeven\n'
                        f'Balance: ${round(account["balance"],2)}'
                    )

                elif trade['tp2'] and price >= trade['tp2'] and not trade['tp2_hit']:
                    pips   = round((trade['tp2']-trade['entry'])*10, 1)
                    profit = round(pips * PIP_VALUE * 0.33, 2)
                    account['balance'] += profit
                    trade['tp2_hit']    = True
                    trade['pnl']       += profit
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'[{LABEL}] TP2 - {trade["id"]}\n'
                        f'+{pips}pips | +${profit}\n'
                        f'Balance: ${round(account["balance"],2)}'
                    )

                elif trade['tp3'] and price >= trade['tp3'] and not trade['tp3_hit']:
                    pips   = round((trade['tp3']-trade['entry'])*10, 1)
                    profit = round(pips * PIP_VALUE * 0.34, 2)
                    account['balance'] += profit
                    trade['tp3_hit']   = True
                    trade['pnl']      += profit
                    trade['status']    = 'closed'
                    trade['time_exit'] = datetime.now(timezone.utc).isoformat()
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'[{LABEL}] TP3 CLOSED - {trade["id"]}\n'
                        f'+{pips}pips | +${profit}\n'
                        f'Total PnL: +${round(trade["pnl"],2)}\n'
                        f'Balance: ${round(account["balance"],2)}'
                    )

        except Exception as e:
            print(f'[C] Price error: {e}')

        await asyncio.sleep(1)

# ── DAILY REPORT ─────────────────────────────────────────
async def daily_report():
    while True:
        now = datetime.now(timezone.utc)
        next_r = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if now >= next_r:
            next_r += timedelta(days=1)
        await asyncio.sleep((next_r - now).total_seconds())

        trades  = load_trades()
        today   = datetime.now(timezone.utc).date().isoformat()
        t_today = [t for t in trades if t['time_entry'].startswith(today)]
        winners = [t for t in t_today if t.get('tp1_hit')]
        losers  = [t for t in t_today if t.get('status') == 'loss']
        bes     = [t for t in t_today if t.get('status') == 'breakeven']
        pnl     = sum(t.get('pnl', 0) for t in t_today)
        ret     = round((account['balance']-account['starting'])/account['starting']*100, 2)
        wr      = round(len(winners)/(len(winners)+len(losers))*100,1) if (winners or losers) else 0

        # Compare all strategies
        try:
            sa = json.load(open('/root/gold-signals/system/state.json'))
            sb = json.load(open('/root/gold-signals/pipeline/state_b.json'))
            comparison = (
                f'\nA vs B vs C\n'
                f'Bal: A=${round(sa["balance"],2)} '
                f'B=${round(sb["balance"],2)} '
                f'C=${round(account["balance"],2)}'
            )
        except:
            comparison = ''

        send_telegram(
            f'[{LABEL}] DAILY - {today}\n'
            f'Trades: {len(t_today)}\n'
            f'W/BE/L: {len(winners)}/{len(bes)}/{len(losers)}\n'
            f'WR: {wr}%\n'
            f'PnL: ${round(pnl,2)}\n'
            f'Balance: ${round(account["balance"],2)}\n'
            f'Return: {ret}%'
            f'{comparison}'
        )

        with open(f'/root/gold-signals/pipeline/report_c_{today}.json', 'w') as f:
            json.dump({'date': today, 'balance': account['balance'],
                      'trades': t_today, 'pnl': pnl}, f, indent=2)

# ── MAIN ─────────────────────────────────────────────────
from telethon import TelegramClient, events

client = TelegramClient(
    '/root/gold-signals/pipeline/gold_session_c',
    api_id, api_hash
)

async def main():
    await client.start()

    send_telegram(
        f'[{LABEL}] STRATEGY C LIVE\n'
        f'Optimised rules:\n'
        f'- Bearish 4H = half size\n'
        f'- Re-entry = 1.5x size\n'
        f'- Sweep = score boost\n'
        f'- Hybrid ML + FVG engine\n'
        f'Balance: ${account["balance"]}\n'
        f'Open: {len(open_trades)}'
    )

    await asyncio.gather(
        signal_processor(),
        price_monitor(),
        daily_report(),
        client.run_until_disconnected()
    )

asyncio.run(main())

