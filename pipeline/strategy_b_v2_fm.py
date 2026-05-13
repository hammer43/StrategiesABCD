import time
last_alert_time = 0
ALERT_COOLDOWN = 180

import asyncio
from session_filter import get_session_info
import json
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from ml.scorer import score_signal

# ── CREDENTIALS ──────────────────────────────────────────
bot_token  = '8680392041:AAFKpVrYzWHQrR-4_-BbmA-eWIC6BV4Zp8s'
my_chat_id = 1273237796
LABEL      = 'Bv2'

# ── FILES ────────────────────────────────────────────────
QUEUE_FILE     = '/root/gold-signals/pipeline/signal_queue.json'
PRICE_FILE     = '/root/gold-signals/pipeline/price.json'
STATE_FILE     = '/root/gold-signals/pipeline/state_b_v2.json'
PROCESSED_FILE = '/root/gold-signals/pipeline/processed_b.json'
TRADES_FILE    = '/root/gold-signals/pipeline/trades_b_v2.json'

# ── CONFIG ───────────────────────────────────────────────
RISK_TOTAL  = 50.0   # $ total risk per signal
SPREAD      = 0.20   # BlackBull spread
FILL_BUFFER = 0.15   # price buffer above zone top
LOT_SIZE    = 0.05   # base lot size

# ── MONDAY PROTECTION (UTC) ───────────────────────────────
MONDAY_SOFT_HOURS = {7, 8, 9, 10, 11}

# ── FORCE MULTIPLIER ──────────────────────────────────────
def get_force_multiplier(conf):
    """
    Returns (mult, reason) based on ML confidence and session timing.

    Monday 07-11 UTC  → 0.5x  protect against Monday morning chop
    ML >= 55%         → 1.5x  high conviction signals
    else              → 1.0x  normal size

    Data basis (38 trades):
      ML >= 55%: 89% WR (27 trades)
      ML <  55%: 70% WR (10 trades)
    """
    now     = datetime.now(timezone.utc)
    weekday = now.weekday()  # 0 = Monday
    hour    = now.hour

    if weekday == 0 and hour in MONDAY_SOFT_HOURS:
        return 0.5, 'MON-PROTECT'
    elif conf >= 55:
        return 1.5, 'HIGH-CONF'
    else:
        return 1.0, 'NORMAL'

# ── STATE ────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            s = json.load(f)
            print(f'[B] Resumed: balance=${s["balance"]} trades={s["total_trades"]}')
            return s
    return {
        'balance': 5000.0, 'starting': 5000.0,
        'pending': None,
        'last_signal_time': None,
        'total_trades': 0, 'total_wins': 0, 'total_losses': 0,
        'total_breakeven': 0
    }

def save_state():
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
        url = f'https://api.telegram.org/bot{bot_token}/sendMessage?chat_id={my_chat_id}&text={msg}'
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
            return [s for s in q if (now - datetime.fromisoformat(s['time'].replace('Z', '+00:00'))).total_seconds() < 600]
    except:
        pass
    return []

def mark_processed(signal_time):
    return

def get_entries(signal, score):
    zone_top = signal['zone_top']
    zone_bot = signal['zone_bot']
    zone_70  = signal['zone_70pct']
    tier     = score['tier']
    entries  = []

    if tier == 'high':
        # 80%+: L1 at zone top, L2 at zone top - 1.0 (10 pips)
        entries = [
            {'price': round(zone_top - 0.1, 2), 'risk': RISK_TOTAL / 2, 'label': 'L1'},
            {'price': zone_top - 1.0,            'risk': RISK_TOTAL / 2, 'label': 'L2'}
        ]
    elif tier == 'mid':
        # 70-79%: L1 at zone top, L2 at 70% of zone
        entries = [
            {'price': round(zone_top - 0.1, 2), 'risk': RISK_TOTAL / 2, 'label': 'L1'},
            {'price': zone_70,                   'risk': RISK_TOTAL / 2, 'label': 'L2'}
        ]
    elif tier == 'low':
        # 60-69%: single entry at zone top
        entries = [
            {'price': round(zone_top - 0.1, 2), 'risk': RISK_TOTAL, 'label': 'L1'}
        ]

    return entries

def open_trade(entry_info, signal, trade_type, force_mult=1.0):
    entry = entry_info['price']
    risk  = entry_info['risk']
    label = entry_info['label']

    # Duplicate check
    for ex in open_trades:
        if ex.get('status') == 'open' and abs(ex.get('entry', 0) - entry) < 0.5 and ex.get('tp1') == signal.get('tp1'):
            print(f'Duplicate prevented: {entry}')
            return

    tid = f"B{account['total_trades']+1:04d}{label}"

    # Scale lots and pip_value by force multiplier
    lots      = round(LOT_SIZE * force_mult, 3)
    pip_value = lots * 10

    sl_pips  = round((entry - signal['sl']) * 10, 1)
    tp1_pips = round((signal['tp1'] - entry) * 10, 1)
    rr       = round(tp1_pips / sl_pips, 2) if sl_pips > 0 else 0

    trade = {
        'id':           tid,
        'strategy':     'B',
        'type':         trade_type,
        'layer':        label,
        'direction':    signal['direction'],
        'entry':        entry,
        'sl':           signal['sl'],
        'sl_original':  signal['sl'],
        'tp1':          signal['tp1'],
        'tp2':          signal['tp2'],
        'tp3':          signal['tp3'],
        'lots':         lots,
        'pip_value':    pip_value,
        'force_mult':   force_mult,
        'risk':         risk,
        'sl_pips':      sl_pips,
        'tp1_pips':     tp1_pips,
        'rr':           rr,
        'out_of_office': signal.get('out_of_office', False),
        'status':       'open',
        'tp1_hit':      False,
        'tp2_hit':      False,
        'tp3_hit':      False,
        'pnl':          0.0,
        'time_entry':   datetime.now(timezone.utc).isoformat(),
        'time_exit':    None
    }
    open_trades.append(trade)
    save_trade(trade)
    account['total_trades'] += 1
    save_state()

    oof_warn = '⚠️ UNMANAGED TRADE\n' if signal.get('out_of_office') else ''
    send_telegram(
        f'[{LABEL}] ✅ {trade_type} — {tid}\n'
        f'{oof_warn}'
        f'Layer: {label}\n'
        f'Entry: {entry}\n'
        f'SL: {signal["sl"]} ({sl_pips} pips)\n'
        f'TP1: {signal["tp1"]} ({tp1_pips} pips)\n'
        f'RR: {rr}\n'
        f'Lots: {lots} ({force_mult}x)\n'
        f'Risk: ${risk}\n'
        f'Balance: ${round(account["balance"], 2)}'
    )

# ── SIGNAL PROCESSOR ─────────────────────────────────────
async def signal_processor():
    processed_times = set()
    if os.path.exists(PROCESSED_FILE):
        try:
            processed_times = set(json.load(open(PROCESSED_FILE)))
        except:
            pass
    zone_cooldowns = {}  # zone_top -> timestamp of last SL hit

    def save_processed():
        tmp = PROCESSED_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(list(processed_times)[-500:], f)
        os.replace(tmp, PROCESSED_FILE)

    while True:
        try:
            queue     = get_queue()
            pdata     = get_price()
            price     = pdata['price'] if pdata else None
            direction = pdata['direction'] if pdata else 'unknown'

            for signal in queue:
                # Skip signals older than 30 minutes
                try:
                    sig_age = (datetime.now(timezone.utc) - datetime.fromisoformat(signal['time'])).total_seconds()
                    if sig_age > 1800:
                        processed_times.add(signal.get('time', ''))
                        continue
                except:
                    pass

                if signal.get('processed'):
                    continue
                if signal.get('time') in processed_times:
                    continue
                if signal.get('type') == 'prepare':
                    processed_times.add(signal['time'])
                    save_processed()
                    continue

                # SL management
                if signal.get('type') == 'sl_manage':
                    new_sl = signal.get('new_sl')
                    if new_sl:
                        for trade in open_trades:
                            if trade['status'] == 'open' and new_sl > trade['sl']:
                                trade['sl'] = new_sl
                                save_trade(trade)
                        send_telegram(f'[{LABEL}] SL → {new_sl}')
                    processed_times.add(signal['time'])
                    save_processed()
                    continue
                if signal.get('type') == 'exit_all':
                    pdata = get_price()
                    price = pdata["price"] if pdata else 0
                    for trade in open_trades:
                        if trade["status"] == "open" and price:
                            pips = round((price - trade["entry"]) * 10, 1)
                            pnl = round(pips * PIP_VALUE, 2)
                            account["balance"] += pnl
                            trade["status"] = "closed"
                            trade["pnl"] = pnl
                            trade["exit"] = price
                            trade["time_exit"] = datetime.now(timezone.utc).isoformat()
                            save_trade(trade)
                    save_state()
                    send_telegram(f"[{LABEL}] EXIT ALL at {price}")
                    processed_times.add(signal.get("time",""))
                    save_processed()
                    continue
                if signal.get('direction') != 'BUY':
                    processed_times.add(signal['time'])
                    save_processed()
                    continue

                # ML score
                zone_key = round(signal.get("zone_top", 0))
                cooldown_time = zone_cooldowns.get(zone_key, 0)
                if datetime.now(timezone.utc).timestamp() - cooldown_time < 1800:
                    processed_times.add(signal.get("time",""))
                    save_processed()
                    send_telegram(f"[{LABEL}] ZONE COOLDOWN {signal["zone_top"]} — skipping")
                    continue
                score = score_signal(signal)
                if score['action'] == 'SKIP':
                    send_telegram(
                        f'[{LABEL}] 🤖 SKIP {score["confidence"]}%\n'
                        f'Zone: {signal["zone_bot"]}-{signal["zone_top"]}'
                    )
                    processed_times.add(signal['time'])
                    save_processed()
                    mark_processed(signal['time'])
                    continue

                # Session filter
                session = get_session_info()
                if not session['trade']:
                    send_telegram(f'[B] SKIP-HOUR: {session["label"]}')
                    processed_times.add(signal['time'])
                    save_processed()
                    mark_processed(signal['time'])
                    continue

                # ── FORCE MULTIPLIER ─────────────────────
                conf = score['confidence']
                force_mult, mult_reason = get_force_multiplier(conf)
                signal['force_mult'] = force_mult

                # Replace stale pending
                if account.get('pending'):
                    old = account['pending']
                    if price and abs(price - old['zone_top']) > 15:
                        send_telegram(
                            f'[{LABEL}] 🗑 Stale pending replaced\n'
                            f'Old: {old["zone_bot"]}-{old["zone_top"]}'
                        )
                        account['pending'] = None
                    else:
                        send_telegram(
                            f'[{LABEL}] ⚠️ Pending active — skipping\n'
                            f'Zone: {signal["zone_bot"]}-{signal["zone_top"]}'
                        )
                        processed_times.add(signal['time'])
                        save_processed()
                        mark_processed(signal['time'])
                        continue

                # Get entry levels
                entries = get_entries(signal, score)
                signal['entries']         = entries
                signal['score']           = score
                signal['entry_direction'] = (
                    'wait_drop' if price and price > signal['fill_trigger']
                    else 'wait_rise' if price and price < signal['zone_bot']
                    else 'in_range'
                )

                # If price already in range — enter immediately
                if signal['entry_direction'] == 'in_range' and price:
                    for entry_info in entries:
                        if price <= entry_info['price'] + 0.15:
                            open_trade(entry_info, signal, 'immediate_entry', force_mult)
                    processed_times.add(signal['time'])
                    save_processed()
                    mark_processed(signal['time'])
                    continue

                # Set as pending
                account['pending'] = signal
                save_state()

                tier_msg = {
                    'high': f'80%+ → L1:{signal["zone_top"]} L2:{signal["zone_top"]-1.0}',
                    'mid':  f'70-79% → L1:{signal["zone_top"]} L2:{signal["zone_70pct"]}',
                    'low':  f'60-69% → Single:{signal["zone_70pct"]}'
                }.get(score['tier'], '')

                dir_msg = {
                    'wait_drop': f'Price above zone ({price}) — waiting for drop ↓',
                    'wait_rise': f'Price below zone ({price}) — waiting for rise ↑'
                }.get(signal['entry_direction'], '')

                oof = '⚠️ OUT OF OFFICE SIGNAL\n' if signal.get('out_of_office') else ''

                send_telegram(
                    f'[{LABEL}] 📡 SIGNAL\n'
                    f'{oof}'
                    f'Zone: {signal["zone_bot"]} — {signal["zone_top"]}\n'
                    f'Fill trigger: {signal["fill_trigger"]}\n'
                    f'TP1: {signal["tp1"]} | SL: {signal["sl"]}\n'
                    f'ML: {round(conf, 1)}%\n'
                    f'Size: {force_mult}x ({mult_reason})\n'
                    f'{tier_msg}\n'
                    f'{dir_msg}'
                )

                processed_times.add(signal['time'])
                save_processed()
                mark_processed(signal['time'])

        except Exception as e:
            print(f'Signal processor error: {e}')

        await asyncio.sleep(5)

# ── PRICE MONITOR ────────────────────────────────────────
async def price_monitor():
    filled_layers = set()

    while True:
        try:
            pdata = get_price()
            if not pdata or not pdata.get('price'):
                await asyncio.sleep(5)
                continue

            price     = pdata['price']
            direction = pdata['direction']

            # ── PENDING ──
            if account.get('pending'):
                p          = account['pending']
                entries    = p.get('entries', [])
                entry_dir  = p.get('entry_direction', 'wait_drop')
                force_mult = p.get('force_mult', 1.0)

                # TP1 already hit — cancel silently
                if price >= p['tp1']:
                    print('SILENT CANCEL: TP1 already hit')
                    account['pending'] = None
                    filled_layers.clear()
                    save_state()

                else:
                    for entry_info in entries:
                        eid    = f'{p["time"]}_{entry_info["label"]}'
                        if eid in filled_layers:
                            continue
                        target = entry_info['price']

                        if entry_dir == 'wait_drop':
                            # Sweep below zone
                            if price < p['zone_bot'] - 0.1:
                                p['entry_direction'] = 'wait_rise'
                                for e in entries:
                                    e['price'] = p['zone_bot']
                                save_state()
                            # Normal fill
                            elif price <= target + FILL_BUFFER:
                                open_trade(entry_info, p, 'limit_drop', force_mult)
                                filled_layers.add(eid)

                        elif entry_dir == 'wait_rise':
                            if price >= target and direction == 'up':
                                open_trade(entry_info, p, 'bounce_rise', force_mult)
                                filled_layers.add(eid)

                    # All layers filled
                    all_filled = all(
                        f'{p["time"]}_{e["label"]}' in filled_layers
                        for e in entries
                    )
                    if all_filled:
                        account['pending'] = None
                        filled_layers.clear()
                        save_state()

            # ── OPEN TRADES ──
            for trade in open_trades:
                if trade['status'] != 'open':
                    continue

                # Use per-trade pip_value (locked in at entry)
                pip_value = trade.get('pip_value', LOT_SIZE * 10)

                # SL hit
                if price <= trade['sl']:
                    is_be = trade.get('tp1_hit', False)
                    if is_be:
                        loss = 0.0
                        account['total_breakeven'] = account.get('total_breakeven', 0) + 1
                        trade['status'] = 'breakeven'
                    else:
                        lots = trade.get('lots', LOT_SIZE)
                        loss = round((trade['entry'] - price) * lots * 100, 2)
                        loss = max(loss, 0)
                        account['balance'] -= loss
                        account['total_losses'] += 1
                        trade['status'] = 'loss'
                    trade['exit']      = price
                    trade['pnl']       = -loss
                    trade['time_exit'] = datetime.now(timezone.utc).isoformat()
                    save_trade(trade)
                    save_state()
                    zone_key = round(trade.get("entry", 0))
                    zone_cooldowns[zone_key] = datetime.now(timezone.utc).timestamp()
                    wr = round(
                        account['total_wins'] / (account['total_wins'] + account['total_losses']) * 100, 1
                    ) if (account['total_wins'] + account['total_losses']) > 0 else 0
                    status_emoji = '〰️ BE' if is_be else '❌ SL'
                    send_telegram(
                        f'[{LABEL}] {status_emoji} — {trade["id"]}\n'
                        f'Entry:{trade["entry"]} Exit:{price}\n'
                        f'Lots: {trade["lots"]} ({trade.get("force_mult", 1.0)}x)\n'
                        f'Loss: -${loss}\n'
                        f'Balance: ${round(account["balance"], 2)}\n'
                        f'WR: {wr}%'
                    )

                # TP1
                elif price >= trade['tp1'] and not trade['tp1_hit']:
                    pips   = round((trade['tp1'] - trade['entry']) * 10, 1)
                    profit = round(pips * pip_value * 0.33, 2)
                    account['balance']    += profit
                    account['total_wins'] += 1
                    trade['tp1_hit'] = True
                    trade['sl']      = trade['entry']   # move SL to breakeven
                    trade['pnl']    += profit
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'[{LABEL}] 🎯 TP1 — {trade["id"]}\n'
                        f'+{pips} pips | +${profit}\n'
                        f'Lots: {trade["lots"]} ({trade.get("force_mult", 1.0)}x)\n'
                        f'SL → Breakeven ✓\n'
                        f'Balance: ${round(account["balance"], 2)}'
                    )

                # TP2
                elif trade['tp2'] and price >= trade['tp2'] and not trade['tp2_hit']:
                    pips   = round((trade['tp2'] - trade['entry']) * 10, 1)
                    profit = round(pips * pip_value * 0.33, 2)
                    account['balance'] += profit
                    trade['tp2_hit']  = True
                    trade['pnl']     += profit
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'[{LABEL}] 🎯 TP2 — {trade["id"]}\n'
                        f'+{pips} pips | +${profit}\n'
                        f'Lots: {trade["lots"]} ({trade.get("force_mult", 1.0)}x)\n'
                        f'Balance: ${round(account["balance"], 2)}'
                    )

                # TP3 — full close
                elif trade['tp3'] and price >= trade['tp3'] and not trade['tp3_hit']:
                    pips   = round((trade['tp3'] - trade['entry']) * 10, 1)
                    profit = round(pips * pip_value * 0.34, 2)
                    account['balance'] += profit
                    trade['tp3_hit']   = True
                    trade['pnl']      += profit
                    trade['status']    = 'closed'
                    trade['exit']      = price
                    trade['time_exit'] = datetime.now(timezone.utc).isoformat()
                    save_trade(trade)
                    save_state()
                    send_telegram(
                        f'[{LABEL}] 🎯 TP3 CLOSED — {trade["id"]}\n'
                        f'+{pips} pips | +${profit}\n'
                        f'Lots: {trade["lots"]} ({trade.get("force_mult", 1.0)}x)\n'
                        f'Total P&L: +${round(trade["pnl"], 2)}\n'
                        f'Balance: ${round(account["balance"], 2)}'
                    )

        except Exception as e:
            print(f'Price monitor error: {e}')

        await asyncio.sleep(5)

# ── DAILY REPORT ─────────────────────────────────────────
async def daily_report():
    while True:
        now         = datetime.now(timezone.utc)
        next_report = now.replace(hour=12, minute=0, second=0, microsecond=0)
        if now >= next_report:
            next_report += timedelta(days=1)
        await asyncio.sleep((next_report - now).total_seconds())

        trades  = load_trades()
        today   = datetime.now(timezone.utc).date().isoformat()
        t_today = [t for t in trades if t['time_entry'].startswith(today)]

        # W = closed (TP3) + tp1_hit open (partial win in progress)
        # BE = breakeven status
        # L = loss status
        winners    = [t for t in t_today if t.get('status') == 'closed']
        partial_w  = [t for t in t_today if t.get('tp1_hit') and t.get('status') == 'open']
        breakevens = [t for t in t_today if t.get('status') == 'breakeven']
        losers     = [t for t in t_today if t.get('status') == 'loss']
        pnl        = sum(t.get('pnl', 0) for t in t_today)
        ret        = round((account['balance'] - account['starting']) / account['starting'] * 100, 2)
        total_w    = len(winners) + len(partial_w)
        wr         = round(total_w / (total_w + len(losers)) * 100, 1) if (total_w or losers) else 0

        forced = [t for t in t_today if t.get('force_mult', 1.0) > 1.0]
        halved = [t for t in t_today if t.get('force_mult', 1.0) < 1.0]

        send_telegram(
            f'[{LABEL}] 📊 DAILY — {today}\n'
            f'Trades: {len(t_today)}\n'
            f'W/BE/L: {total_w}/{len(breakevens)}/{len(losers)}\n'
            f'  (Closed: {len(winners)} | TP1+ open: {len(partial_w)})\n'
            f'WR: {wr}%\n'
            f'P&L: ${round(pnl, 2)}\n'
            f'Balance: ${round(account["balance"], 2)}\n'
            f'Return: {ret}%\n'
            f'1.5x trades: {len(forced)} | 0.5x trades: {len(halved)}\n'
            f'Total W/BE/L: {account["total_wins"]}/{account.get("total_breakeven",0)}/{account["total_losses"]}'
        )

        with open(f'/root/gold-signals/pipeline/report_b_{today}.json', 'w') as f:
            json.dump({'date': today, 'balance': account['balance'],
                       'trades': t_today, 'pnl': pnl}, f, indent=2)

# ── MAIN ─────────────────────────────────────────────────
async def main():
    send_telegram(
        f'🟢 STRATEGY B LIVE\n'
        f'ML>=55% → 1.5x size | Mon 7-11 UTC → 0.5x\n'
        f'60%+: zone top single entry\n'
        f'70%+: L1 zone top + L2 70% zone\n'
        f'80%+: L1 zone top + L2 zone top-10pip\n'
        f'Spread buffer: {FILL_BUFFER}\n'
        f'Base lots: {LOT_SIZE}\n'
        f'Balance: ${account["balance"]}\n'
        f'Open trades: {len(open_trades)}'
    )
    await asyncio.gather(
        signal_processor(),
        price_monitor(),
        daily_report()
    )

asyncio.run(main())
