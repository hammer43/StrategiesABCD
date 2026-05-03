import asyncio
import json
import re
import os
import threading
import urllib.request
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, events
from price_engine import PriceEngine
from ml_scorer import score_signal

api_id      = 29091418
api_hash    = '3d1d32748831ac3d51991d24d03623bd'
channel_id  = -1003293033145
bot_token   = '8680392041:AAFKpVrYzWHQrR-4_-BbmA-eWIC6BV4Zp8s'
my_chat_id  = 1273237796

RISK_PER_TRADE   = 50
LOT_SIZE         = 0.05
PIP_VALUE        = LOT_SIZE * 10
BREAKEVEN_BUFFER = 0.2
STALE_PIPS       = 15
STATE_FILE       = '/root/gold-signals/system/state.json'
TRADES_FILE      = '/root/gold-signals/system/trades.json'

IGNORE_KEYWORDS = [
    'RISK FREE', 'SL TO BE', 'CLOSE +', 'SECURING',
    'PARTIAL', 'TP1 HIT', 'TP2 HIT', 'TP3 HIT',
    'SL HIT', 'BREAKEVEN', 'MOVE SL', 'LOCKED IN',
    'TRAILING', 'OUT OF OFFICE', 'NOT AROUND',
    'AT YOUR OWN RISK', 'PIPS'
]

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
            print(f'Resumed: balance={s["balance"]} trades={s["total_trades"]}')
            return s
    return {
        'balance': 5000.0, 'starting': 5000.0,
        'pending': None,
        'total_trades': 0, 'total_wins': 0,
        'total_losses': 0, 'total_breakeven': 0
    }

def save_state():
    with open(STATE_FILE, 'w') as f:
        json.dump(account, f, indent=2)

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
engine      = PriceEngine()

def send_telegram(message):
    try:
        msg = urllib.request.quote(str(message))
        url = (f'https://api.telegram.org/bot{bot_token}'
               f'/sendMessage?chat_id={my_chat_id}&text={msg}')
        urllib.request.urlopen(url, timeout=5)
    except Exception as e:
        print(f'Telegram error: {e}')

def is_management_message(text):
    upper = text.upper()
    return any(kw in upper for kw in IGNORE_KEYWORDS)

def is_trading_hour():
    hour = datetime.now(timezone.utc).hour
    if hour == 16:
        return False, 0.5
    if hour in [10, 11, 19, 20, 21]:
        return True, 1.0
    if hour in [5, 6, 7, 8, 9]:
        return True, 1.0
    if hour in [12, 13, 14, 15, 17, 18]:
        return True, 0.5
    return True, 0.5

def calculate_lots(entry, sl, risk):
    price_dist = abs(entry - sl)
    if price_dist == 0:
        return 0.01
    lots = round(risk / (price_dist * 100), 2)
    return max(0.01, min(lots, 1.0))

def parse_signal(text):
    try:
        zone = re.search(r'(\d{4}\.?\d*)/(\d{4}\.?\d*)', text)
        tps  = re.findall(r'TP\s+(\d{4}\.?\d*)', text)
        sl   = re.search(r'SL\s+(\d{4}\.?\d*)', text)
        if zone and tps and sl:
            zt = float(zone.group(1))
            zb = float(zone.group(2))
            zone_top = max(zt, zb)
            zone_bot = min(zt, zb)
            tp_levels = [float(t) for t in tps]
            sl_val = float(sl.group(1))
            zone_width = zone_top - zone_bot
            return {
                'zone_top':     zone_top,
                'zone_bot':     zone_bot,
                'zone_width':   zone_width,
                'zone_70pct':   round(zone_bot + zone_width * 0.70, 2),
                'fill_trigger': round(zone_top + 0.15, 2),
                'tp1': tp_levels[0],
                'tp2': tp_levels[1] if len(tp_levels) > 1 else None,
                'tp3': tp_levels[2] if len(tp_levels) > 2 else None,
                'sl':  sl_val,
                'high_risk': 'HIGH RISK' in text.upper(),
                'out_of_office': any(k in text.upper() for k in [
                    'OUT OF OFFICE', 'NOT AROUND', 'AT YOUR OWN RISK'
                ]),
                'time': datetime.now(timezone.utc).isoformat(),
                'price_at_signal': engine.current_price
            }
    except Exception as e:
        print(f'Parse error: {e}')
    return None

def get_entry_direction(signal, price):
    if price is None:
        return 'wait_drop'
    if price > signal['fill_trigger']:
        return 'wait_drop'
    elif price < signal['zone_bot']:
        return 'wait_rise'
    return 'in_range'

def open_trade(entry, signal, trade_type, risk, label='L1'):
    lots = calculate_lots(entry, signal['sl'], risk)
    # Duplicate check
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    for ex in load_trades():
        if ex.get("tp1") == signal.get("tp1") and ex.get("time_entry","") > cutoff:
            print(f"Duplicate prevented tp1={signal.get("tp1")}")
            return
    sl_pips  = round((entry - signal['sl']) * 10, 1)
    tp1_pips = round((signal['tp1'] - entry) * 10, 1)
    rr = round(tp1_pips / sl_pips, 2) if sl_pips > 0 else 0
    tid = f'T{account["total_trades"]+1:04d}{label}'

    trade = {
        'id': tid, 'type': trade_type, 'layer': label,
        'direction': 'BUY',
        'entry': entry,
        'sl': round(signal['sl'] - 0.2, 2),
        'sl_original': signal['sl'],
        'tp1': round(signal['tp1'] - 0.3, 2),
        'tp2': signal['tp2'],
        'tp3': signal['tp3'],
        'lots': lots, 'risk': risk,
        'sl_pips': sl_pips, 'tp1_pips': tp1_pips, 'rr': rr,
        'out_of_office': signal.get('out_of_office', False),
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
        f'TRADE {label} - {tid}\n'
        f'Type: {trade_type}\n'
        f'Entry: {entry}\n'
        f'SL: {signal["sl"]} ({sl_pips} pips)\n'
        f'TP1: {signal["tp1"]} ({tp1_pips} pips)\n'
        f'RR: {rr}\n'
        f'Risk: {risk}\n'
        f'Balance: {round(account["balance"],2)}'
    )

async def price_monitor():
    await asyncio.sleep(3)
    filled_layers = set()

    while True:
        try:
            price     = engine.current_price
            direction = engine.get_direction()

            if price is None:
                await asyncio.sleep(1)
                continue

            if account.get('pending'):
                p         = account['pending']
                entry_dir = p.get('entry_direction', 'wait_drop')
                entries   = p.get('entries', [])

                if price >= p['tp1']:
                    send_telegram(
                        f'PENDING CANCELLED\n'
                        f'TP1 {p["tp1"]} already hit\n'
                        f'Price: {price} trap avoided'
                    )
                    account['pending'] = None
                    filled_layers.clear()
                    save_state()

                elif entry_dir == 'wait_drop' and price < p['zone_bot'] - STALE_PIPS * 0.1:
                    send_telegram(f'PENDING EXPIRED\nPrice too far: {price}')
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
                            elif price <= target + 0.15:
                                open_trade(target, p, 'limit_drop',
                                          entry_info['risk'], entry_info['label'])
                                filled_layers.add(eid)

                        elif entry_dir == 'wait_rise':
                            if price >= target and direction == 'up':
                                open_trade(target, p, 'bounce_rise',
                                          entry_info['risk'], entry_info['label'])
                                filled_layers.add(eid)

                        elif entry_dir == 'in_range':
                            if direction == 'up' and price <= target + 0.15:
                                open_trade(target, p, 'zone_entry',
                                          entry_info['risk'], entry_info['label'])
                                filled_layers.add(eid)

                    if entries and all(
                        f'{p["time"]}_{e["label"]}' in filled_layers
                        for e in entries
                    ):
                        account['pending'] = None
                        filled_layers.clear()
                        save_state()

            for trade in open_trades:
                if trade['status'] != 'open':
                    continue

                if price <= trade['sl']:
                    is_be = trade.get('tp1_hit', False)
                    loss  = round(abs(trade['entry'] - trade['sl']) * trade['lots'] * 100, 2)

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
                    wr = round(account['total_wins']/total*100, 1) if total > 0 else 0
                    status = 'BREAKEVEN' if is_be else 'STOP LOSS'
                    send_telegram(
                        f'{status} - {trade["id"]}\n'
                        f'Entry: {trade["entry"]} Exit: {price}\n'
                        f'PnL: {round(trade["pnl"],2)}\n'
                        f'Balance: {round(account["balance"],2)}\n'
                        f'WR: {wr}%'
                    )

                elif price >= trade['tp1'] and not trade['tp1_hit']:
                    pips   = round((trade['tp1'] - trade['entry']) * 10, 1)
                    profit = round(pips * PIP_VALUE * 1.0, 2)
                    account['balance']    += profit
                    account['total_wins'] += 1
                    trade['tp1_hit'] = True
                    trade['sl']      = round(trade['entry'] - BREAKEVEN_BUFFER, 2)
                    trade["status"] = "closed"
                    trade["time_exit"] = datetime.now(timezone.utc).isoformat()
                    trade['pnl']    += profit
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'TP1 - {trade["id"]}\n'
                        f'+{pips} pips | +{profit}\n'
                        f'SL Breakeven\n'
                        f'Balance: {round(account["balance"],2)}'
                    )

                elif trade['tp2'] and price >= trade['tp2'] and not trade['tp2_hit']:
                    pips   = round((trade['tp2'] - trade['entry']) * 10, 1)
                    profit = round(pips * PIP_VALUE * 0.33, 2)
                    account['balance'] += profit
                    trade['tp2_hit']    = True
                    trade['pnl']       += profit
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'TP2 - {trade["id"]}\n'
                        f'+{pips} pips | +{profit}\n'
                        f'Balance: {round(account["balance"],2)}'
                    )

                elif trade['tp3'] and price >= trade['tp3'] and not trade['tp3_hit']:
                    pips   = round((trade['tp3'] - trade['entry']) * 10, 1)
                    profit = round(pips * PIP_VALUE * 0.34, 2)
                    account['balance'] += profit
                    trade['tp3_hit']   = True
                    trade['pnl']      += profit
                    trade['status']    = 'closed'
                    trade['time_exit'] = datetime.now(timezone.utc).isoformat()
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'TP3 CLOSED - {trade["id"]}\n'
                        f'+{pips} pips | +{profit}\n'
                        f'Total PnL: +{round(trade["pnl"],2)}\n'
                        f'Balance: {round(account["balance"],2)}'
                    )

        except Exception as e:
            print(f'Monitor error: {e}')

        await asyncio.sleep(1)

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
        wr      = round(len(winners)/(len(winners)+len(losers))*100, 1) if (winners or losers) else 0

        send_telegram(
            f'DAILY REPORT - {today}\n'
            f'Trades: {len(t_today)}\n'
            f'Winners: {len(winners)}\n'
            f'Breakeven: {len(bes)}\n'
            f'Losers: {len(losers)}\n'
            f'WR: {wr}%\n'
            f'PnL: {round(pnl,2)}\n'
            f'Balance: {round(account["balance"],2)}\n'
            f'Return: {ret}%\n'
            f'W/BE/L: {account["total_wins"]}/{account.get("total_breakeven",0)}/{account["total_losses"]}'
        )

        with open(f'/root/gold-signals/system/report_{today}.json', 'w') as f:
            json.dump({'date': today, 'balance': account['balance'],
                      'trades': t_today, 'pnl': pnl}, f, indent=2)

STARTUP_TIME = datetime.now(timezone.utc)
client = TelegramClient("gold_session_a", api_id, api_hash)

processed_message_ids = set()

@client.on(events.NewMessage(chats=channel_id))
async def handler(event):
    text = event.message.text
    if event.message.id in processed_message_ids:
        return
    processed_message_ids.add(event.message.id)
    if event.message.date.astimezone(timezone.utc) < STARTUP_TIME:
        return
    if not text:
        return
    upper = text.upper()

    if is_management_message(text):
        print(f'IGNORE: {text[:50]}')
        return

    if any(k in upper for k in ['SELL GOLD', 'SELL XAUUSD']) and 'SL' in upper:
        for trade in open_trades:
            if trade['status'] == 'open':
                price = engine.current_price or trade['entry']
                pnl = round((price-trade['entry'])*trade['lots']*100, 2)
                account['balance'] += pnl
                trade['status']    = 'closed_bias_flip'
                trade['exit']      = price
                trade['pnl']       = pnl
                trade['time_exit'] = datetime.now(timezone.utc).isoformat()
                save_trade(trade)
                send_telegram(f'BIAS FLIP - {trade["id"]} closed PnL: {pnl}')
        account['pending'] = None
        save_state()
        return

    if any(k in upper for k in ['BUY GOLD','BUY XAUUSD','BUY LIMITS GOLD']) and 'SL' in upper and 'TP' in upper:
        signal = parse_signal(text)
        if not signal:
            return

        score = score_signal(signal)
        conf  = score['confidence']
        tier  = score['tier']

        if conf < 40:
            send_telegram(f'ML SKIP {conf}% Zone: {signal["zone_bot"]}-{signal["zone_top"]}')
            return

        tradeable, size_mult = is_trading_hour()
        if not tradeable:
            send_telegram(f'SKIP bad hour Zone: {signal["zone_bot"]}-{signal["zone_top"]}')
            return

        if account.get('pending'):
            old = account['pending']
            price = engine.current_price
            if price and abs(price - old['zone_top']) > STALE_PIPS * 0.1:
                send_telegram('Stale pending replaced')
                account['pending'] = None
            else:
                send_telegram(f'Pending active skipping Zone: {signal["zone_bot"]}-{signal["zone_top"]}')
                return

        price    = engine.current_price
        zone_top = signal['zone_top']
        zone_70  = signal['zone_70pct']
        base_risk = RISK_PER_TRADE * size_mult

        if tier == 'high':
            entries = [
                {'price': zone_top,       'risk': base_risk/2, 'label': 'L1'},
                {'price': zone_top - 1.0, 'risk': base_risk/2, 'label': 'L2'}
            ]
        elif tier == 'mid':
            entries = [
                {'price': zone_top, 'risk': base_risk/2, 'label': 'L1'},
                {'price': zone_70,  'risk': base_risk/2, 'label': 'L2'}
            ]
        else:
            entries = [
                {'price': zone_70, 'risk': base_risk, 'label': 'L1'}
            ]

        entry_dir = get_entry_direction(signal, price)
        signal['entries']         = entries
        signal['entry_direction'] = entry_dir
        signal['score']           = score
        account['pending']        = signal
        save_state()

        send_telegram(
            f'SIGNAL {tier.upper()} {conf}%\n'
            f'Zone: {signal["zone_bot"]} - {signal["zone_top"]}\n'
            f'TP1: {signal["tp1"]} SL: {signal["sl"]}\n'
            f'Entries: {[e["price"] for e in entries]}\n'
            f'Direction: {entry_dir}'
        )

async def main():
    engine.start()
    await asyncio.sleep(3)
    await client.start()

    open_count = len([t for t in load_trades() if t.get('status') == 'open'])
    send_telegram(
        f'STRATEGY A v3 LIVE\n'
        f'Balance: {account["balance"]}\n'
        f'Open: {open_count}\n'
        f'Trades: {account["total_trades"]}\n'
        f'W/BE/L: {account["total_wins"]}/{account.get("total_breakeven",0)}/{account["total_losses"]}'
    )

    await asyncio.gather(
        price_monitor(),
        daily_report(),
        client.run_until_disconnected()
    )

asyncio.run(main())
