import asyncio
import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta

bot_token  = '8680392041:AAFKpVrYzWHQrR-4_-BbmA-eWIC6BV4Zp8s'
my_chat_id = 1273237796
LABEL      = 'RAW'
QUEUE_FILE = '/root/gold-signals/pipeline/signal_queue.json'
PRICE_FILE = '/root/gold-signals/pipeline/price.json'
STATE_FILE = '/root/gold-signals/pipeline/state_raw.json'
TRADES_FILE= '/root/gold-signals/pipeline/trades_raw.json'
LOT_SIZE   = 0.05
PIP_VALUE  = LOT_SIZE * 10
BREAKEVEN_BUFFER = 0.2

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            try:
                return json.load(f)
            except:
                pass
    return {
        'balance': 5000.0, 'starting': 5000.0,
        'pending': None,
        'total_trades': 0, 'total_wins': 0,
        'total_losses': 0, 'total_breakeven': 0
    }

def save_state():
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(account, f, indent=2)
    os.replace(tmp, STATE_FILE)

def load_trades():
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f:
            try:
                return json.load(f)
            except:
                return []
    return []

def save_trade(trade):
    trades = load_trades()
    trades = [t for t in trades if t['id'] != trade['id']]
    trades.append(trade)
    tmp = TRADES_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(trades, f, indent=2)
    os.replace(tmp, TRADES_FILE)

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
            return [s for s in q if (now - datetime.fromisoformat(
                s['time'].replace('Z','+00:00'))).total_seconds() < 600]
    except:
        pass
    return []

def send_telegram(message):
    try:
        msg = urllib.request.quote(str(message))
        url = f'https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={my_chat_id}&text={msg}'
        urllib.request.urlopen(url, timeout=5)
    except Exception as e:
        print(f'Telegram error: {e}')

account     = load_state()
open_trades = [t for t in load_trades() if t.get('status') == 'open']

def open_trade(entry, signal, trade_type):
    sl   = signal['sl']
    for ex in open_trades:
        if ex.get("status") == "open" and abs(ex.get("entry",0) - entry) < 0.5 and ex.get("tp1") == signal.get("tp1"):
            print(f"Duplicate prevented: {entry}")
            return
    tp1  = signal['tp1']
    tp2  = signal.get('tp2')
    tp3  = signal.get('tp3')
    dist = abs(entry - sl)
    lots = round(50 / (dist * 100), 2) if dist > 0 else 0.01
    lots = max(0.01, min(lots, 1.0))
    sl_pips  = round(dist * 10, 1)
    tp1_pips = round((tp1 - entry) * 10, 1)
    tid = f'R{account["total_trades"]+1:04d}'

    trade = {
        'id': tid, 'type': trade_type,
        'entry': entry, 'sl': sl, 'sl_original': sl,
        'tp1': tp1, 'tp2': tp2, 'tp3': tp3,
        'lots': lots, 'sl_pips': sl_pips, 'tp1_pips': tp1_pips,
        'status': 'open',
        'tp1_hit': False, 'tp2_hit': False, 'tp3_hit': False,
        'pnl': 0.0,
        'signal_age': signal.get('age_seconds', 0),
        'time_entry': datetime.now(timezone.utc).isoformat(),
        'time_exit': None
    }
    open_trades.append(trade)
    save_trade(trade)
    account['total_trades'] += 1
    save_state()

    send_telegram(f'[{LABEL}] TRADE {tid}\n'
                  f'Entry: {entry}\n'
                  f'SL: {sl} ({sl_pips}pips)\n'
                  f'TP1: {tp1} ({tp1_pips}pips)\n'
                  f'Signal age: {signal.get("age_seconds",0)}s\n'
                  f'Balance: ${round(account["balance"],2)}')

async def signal_processor():
    seen = set()
    while True:
        try:
            queue = get_queue()
            pdata = get_price()
            price = pdata['price'] if pdata else None

            for signal in queue:
                sig_time = signal.get('time','')
                if sig_time in seen:
                    continue
                if signal.get('type') not in ['buy','sell','buy_limit','sell_limit']:
                    seen.add(sig_time)
                    continue
                if signal.get('direction') != 'BUY':
                    seen.add(sig_time)
                    continue

                seen.add(sig_time)

                if account.get('pending'):
                    seen.add(sig_time)
                    continue

                zone_top = signal['zone_top']
                entry    = round(zone_top - 0.1, 2)

                # Check if TP1 already hit
                if price and price >= signal['tp1']:
                    send_telegram(f'[{LABEL}] SKIP - TP1 already hit\n'
                                  f'TP1: {signal["tp1"]} Price: {price}\n'
                                  f'Signal age: {signal.get("age_seconds",0)}s')
                    continue

                signal['entry_price']     = entry
                signal['entry_direction'] = 'wait_drop' if (price and price > zone_top + 0.5) else 'in_range'
                account['pending']        = signal
                save_state()

                send_telegram(f'[{LABEL}] PENDING SET\n'
                              f'Zone: {signal["zone_bot"]}-{zone_top}\n'
                              f'Entry: {entry}\n'
                              f'TP1: {signal["tp1"]}\n'
                              f'SL: {signal["sl"]}\n'
                              f'Age: {signal.get("age_seconds",0)}s\n'
                              f'Direction: {signal["entry_direction"]}')

        except Exception as e:
            print(f'[RAW] Signal error: {e}')
        await asyncio.sleep(1)

async def price_monitor():
    filled = set()
    while True:
        try:
            pdata     = get_price()
            if not pdata or not pdata.get('price'):
                await asyncio.sleep(1)
                continue
            price     = pdata['price']
            direction = pdata['direction']

            if account.get('pending'):
                p      = account['pending']
                entry  = p.get('entry_price', p['zone_top'] - 0.1)
                entry_dir = p.get('entry_direction', 'wait_drop')

                if price >= p['tp1']:
                    send_telegram(f'[{LABEL}] CANCELLED - TP1 hit\nPrice: {price}')
                    account['pending'] = None
                    save_state()

                elif entry_dir == 'wait_drop' and price <= entry + 0.15:
                    open_trade(entry, p, 'limit_drop')
                    account['pending'] = None
                    save_state()

                elif entry_dir == 'in_range' and price <= entry + 0.15:
                    open_trade(entry, p, 'zone_entry')
                    account['pending'] = None
                    save_state()

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
                    status = 'BREAKEVEN' if is_be else 'STOP LOSS'
                    send_telegram(f'[{LABEL}] {status} {trade["id"]}\n'
                                  f'Entry:{trade["entry"]} Exit:{price}\n'
                                  f'PnL: ${round(trade["pnl"],2)}\n'
                                  f'Balance: ${round(account["balance"],2)}')

                elif price >= trade['tp1'] and not trade['tp1_hit']:
                    pips   = round((trade['tp1']-trade['entry'])*10,1)
                    profit = round(pips*PIP_VALUE*0.33,2)
                    account['balance']    += profit
                    account['total_wins'] += 1
                    trade['tp1_hit'] = True
                    trade['sl']      = round(trade['entry'] - BREAKEVEN_BUFFER, 2)
                    trade['pnl']    += profit
                    save_trade(trade)
                    save_state()
                    send_telegram(f'[{LABEL}] TP1 {trade["id"]}\n'
                                  f'+{pips}pips +${profit}\n'
                                  f'Balance: ${round(account["balance"],2)}')

                elif trade['tp2'] and price >= trade['tp2'] and not trade['tp2_hit']:
                    pips   = round((trade['tp2']-trade['entry'])*10,1)
                    profit = round(pips*PIP_VALUE*0.33,2)
                    account['balance'] += profit
                    trade['tp2_hit']    = True
                    trade['pnl']       += profit
                    save_trade(trade)
                    save_state()
                    send_telegram(f'[{LABEL}] TP2 {trade["id"]}\n'
                                  f'+{pips}pips +${profit}\n'
                                  f'Balance: ${round(account["balance"],2)}')

                elif trade['tp3'] and price >= trade['tp3'] and not trade['tp3_hit']:
                    pips   = round((trade['tp3']-trade['entry'])*10,1)
                    profit = round(pips*PIP_VALUE*0.34,2)
                    account['balance'] += profit
                    trade['tp3_hit']   = True
                    trade['pnl']      += profit
                    trade['status']    = 'closed'
                    trade['time_exit'] = datetime.now(timezone.utc).isoformat()
                    save_trade(trade)
                    save_state()
                    send_telegram(f'[{LABEL}] TP3 CLOSED {trade["id"]}\n'
                                  f'+{pips}pips +${profit}\n'
                                  f'Balance: ${round(account["balance"],2)}')

        except Exception as e:
            print(f'[RAW] Price error: {e}')
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
        pnl     = sum(t.get('pnl',0) for t in t_today)
        ret     = round((account['balance']-account['starting'])/account['starting']*100,2)
        wr      = round(len(winners)/(len(winners)+len(losers))*100,1) if (winners or losers) else 0

        send_telegram(f'[{LABEL}] DAILY {today}\n'
                     f'Trades: {len(t_today)}\n'
                     f'W/BE/L: {len(winners)}/{len(bes)}/{len(losers)}\n'
                     f'WR: {wr}%\n'
                     f'PnL: ${round(pnl,2)}\n'
                     f'Balance: ${round(account["balance"],2)}\n'
                     f'Return: {ret}%')

async def main():
    send_telegram(f'[{LABEL}] RAW STRATEGY LIVE\n'
                  f'No filters - takes all signals\n'
                  f'Entry: zone_top - 0.1\n'
                  f'Balance: ${account["balance"]}')
    await asyncio.gather(
        signal_processor(),
        price_monitor(),
        daily_report()
    )

asyncio.run(main())
