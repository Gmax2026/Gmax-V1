"""
G MAX V1 — Signal Bot (Production Build)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Pure Python | Termux & PC compatible
pip install flask requests

Reads signals from Telegram channel → opens futures trades
on Binance, Bitget, Bybit, KuCoin, OKX via direct exchange APIs
(requests + hmac/hashlib — no ccxt).

Engineered by Paqu
Server Hosted by Paqu
Strategy by Paqu
2 Years Backtested by Paqu
"""

import json, time, logging, threading, re, socket, requests, math
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from flask import Flask, render_template_string, request, redirect, jsonify

BOT_VERSION  = "2026.08.25.2"   # bumped by Paqu on each release pushed to Gmax-V1 repo
BOT_DIR      = Path(__file__).parent
CONFIG_PATH  = BOT_DIR / "config.json"
HISTORY_PATH = BOT_DIR / "trade_history.json"
POSITIONS_PATH = BOT_DIR / "open_positions.json"

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s | %(message)s', datefmt='%I:%M %p')
log = logging.getLogger(__name__)

SIGNAL_CHANNEL_ID  = -1003735136214
LICENSE_SERVER_URL = 'https://gmax-license.onrender.com'

# ── Integrity check ─────────────────────────────────────────
# Verifies this file's contents against the hash of the official release,
# held on the license server (which customers cannot edit). This runs
# alongside the normal license check, not standalone — a modified file
# can still start, but license_check() will refuse to validate it and
# the bot will show your Contact Us info instead of trading.
import hashlib as _hashlib

def _file_hash():
    try:
        with open(__file__, 'rb') as _f:
            return _hashlib.sha256(_f.read()).hexdigest()
    except Exception:
        return ''

# ── Exchange config ────────────────────────────────────────
EXCHANGES = ['binance', 'bitget', 'bybit', 'kucoin', 'okx']
EXCHANGE_LABELS = {
    'binance': 'Binance',
    'bitget' : 'Bitget',
    'bybit'  : 'Bybit',
    'kucoin' : 'KuCoin',
    'okx'    : 'OKX',
}

# ── Default Settings ───────────────────────────────────────
DEFAULT_SETTINGS = {
    'margin_usd'    : 10.0,
    'margin_percent': 2.0,
    'margin_mode'   : 'fixed',
    'leverage'      : 5,
    'margin_type'   : 'CROSSED',
    'cooldown_min'  : 5,
    'theme'         : 'classic',
    'license_expiry': '',
    'license_valid' : False,
}
THEMES = ['classic','cyber','aurora','solar','matrix','sunset',
          'arceus_white','pikachu_strike','lucario','charizard']
THEME_LABELS = {
    'classic'       : '⬛ Classic',
    'cyber'         : '🟣 Neon Cyber',
    'aurora'        : '🌊 Aurora',
    'solar'         : '☀️ Solar (Light)',
    'matrix'        : '💚 Matrix',
    'sunset'        : '🌅 Sunset',
    'arceus_white'  : '⚪ Arceus White',
    'pikachu_strike': '⚡ Pikachu Strike',
    'lucario'       : '🔵 Lucario',
    'charizard'     : '🔥 Charizard',
}

# ── Config helpers ─────────────────────────────────────────
def load_config():
    if not CONFIG_PATH.exists(): return {}
    try:
        with open(CONFIG_PATH) as f: return json.load(f)
    except Exception: return {}

def save_config_data(data):
    existing = load_config()
    existing.update(data)
    with open(CONFIG_PATH, 'w') as f: json.dump(existing, f, indent=2)

def get_settings():
    cfg = load_config()
    return {k: cfg.get(k, DEFAULT_SETTINGS[k]) for k in DEFAULT_SETTINGS}

def current_theme():
    t = load_config().get('theme', 'classic')
    return t if t in THEMES else 'classic'

def is_configured():
    cfg = load_config()
    has_exchange = any(
        cfg.get(f'{ex}_key','') and cfg.get(f'{ex}_key','') not in ('','YOUR_KEY')
        for ex in EXCHANGES
    )
    has_tg = bool(cfg.get('tg_token','')) and bool(cfg.get('tg_chat_id',''))
    # Token IS the license — tg_token serves as both bot token and license identity
    return has_exchange and has_tg

def get_enabled_exchanges():
    cfg = load_config()
    result = []
    for ex in EXCHANGES:
        key    = cfg.get(f'{ex}_key','').strip()
        secret = cfg.get(f'{ex}_secret','').strip()
        enabled = cfg.get(f'{ex}_enabled', True)
        if key and secret and enabled:
            result.append(ex)
    return result

def exchange_has_credentials(ex):
    cfg = load_config()
    return bool(cfg.get(f'{ex}_key','').strip()) and bool(cfg.get(f'{ex}_secret','').strip())

# ── Time helpers ───────────────────────────────────────────
def _t():  return datetime.now().astimezone().strftime('%I:%M %p')
def _dt(): return datetime.now().astimezone().strftime('%b %d %I:%M %p')
def _date(): return datetime.now().astimezone().strftime('%Y-%m-%d')

# ── State ──────────────────────────────────────────────────
state = {
    'open_positions' : {},
    'trades'         : [],
    'total_pnl'      : 0.0,
    'today_pnl'      : 0.0,
    'wins'           : 0,
    'losses'         : 0,
    'last_signal'    : None,
    'exchange_status': {},
    'balance_cache'  : {},
    'start_time'     : datetime.now().astimezone().strftime('%b %d %I:%M %p'),
    'running'        : False,
    'signal_count'   : 0,
    'tg_poll_offset' : 0,
    'last_error'     : '',
}

# ── License & Device ──────────────────────────────────────
import platform, hashlib as _hl, os as _os

def _device_id():
    """
    Multi-factor device fingerprint.
    Combines hostname + CPU arch + OS + Python path + install dir.
    Hard to fake all at once. Works on Android/Termux and PC.
    """
    try:
        node     = platform.node()         or 'unknown'
        machine  = platform.machine()      or 'unknown'
        system   = platform.system()       or 'unknown'
        release  = platform.release()      or 'unknown'
        py_path  = _os.path.dirname(_os.path.abspath(__file__))
        # Try to get CPU count as extra factor
        try:    cpu = str(_os.cpu_count() or 1)
        except: cpu = '1'
        raw = f"{node}|{machine}|{system}|{release}|{cpu}|{py_path}"
        return _hl.sha256(raw.encode()).hexdigest()[:24]
    except Exception:
        # Fallback — at least use hostname
        return _hl.sha256(platform.node().encode()).hexdigest()[:24]

def _device_name():
    try:
        system = platform.system()
        node   = platform.node()
        machine= platform.machine()
        if system == 'Linux' and 'ANDROID_ROOT' in _os.environ:
            label = node if node else _device_id()[:8]
            return f"Android/{label}"[:40]
        label = node if node else _device_id()[:8]
        return f"{system} {label} ({machine})"[:40]
    except Exception:
        return f"Unknown ({_device_id()[:8]})"

def _update_command():
    """Same one-line command customers used to install the bot the first
    time — running it again re-downloads the latest GMaxSignalBot.py and
    is safe to run while the bot is stopped or running."""
    try:
        if platform.system() == 'Linux' and 'ANDROID_ROOT' in _os.environ:
            return ('pkg install curl -y && curl -fsSL '
                    'https://raw.githubusercontent.com/Gmax2026/Gmax-V1/main/setup_mobile.sh | bash')
    except Exception:
        pass
    return ('curl -fsSL '
            'https://raw.githubusercontent.com/Gmax2026/Gmax-V1/main/setup_pc.sh | bash')

_license_cache = {'valid': False, 'checked_at': 0, 'expiry': ''}
_contact_cache = {'contacts': [], 'checked_at': 0}

def _fetch_contact_info():
    """Fetch Contact Us info (Telegram/FB/WhatsApp/Email) from the license server.
    Cached for 10 minutes so we don't hit the server on every page load, and falls
    back to the last known-good copy if the server is briefly unreachable."""
    global _contact_cache
    if time.time() - _contact_cache['checked_at'] < 600 and _contact_cache['contacts']:
        return _contact_cache['contacts']
    try:
        r = requests.get(f"{LICENSE_SERVER_URL}/contact-info", timeout=10)
        data = r.json()
        contacts = data.get('contacts', [])
        if contacts:
            _contact_cache = {'contacts': contacts, 'checked_at': time.time()}
        return contacts
    except Exception as e:
        log.info(f"contact info fetch error: {e}")
        return _contact_cache['contacts']


_pending_update = {'version': None, 'whats_new': ''}

def license_check(force=False):
    global _license_cache
    now = time.time()
    if not force and (now - _license_cache.get('checked_at', 0)) < 1800:
        return _license_cache.get('valid', False)

    cfg = load_config()
    token = cfg.get('tg_token', '').strip()
    if not token:
        _license_cache = {'valid': False, 'checked_at': now, 'reason': 'no_token'}
        return False

    # Get real stats
    try:
        ov = _overall_stats()
    except Exception:
        ov = {'total_pnl': 0.0, 'today_pnl': 0.0}

    # Get open positions and trade count
    try:
        open_pos    = len(state.get('open_positions', {}))
        total_trades= len(state.get('trades', []))
    except Exception:
        open_pos = 0; total_trades = 0

    for attempt in range(2):
        try:
            r = requests.post(f"{LICENSE_SERVER_URL}/validate", json={
                'token'         : token,
                'device_id'     : _device_id(),
                'file_hash'     : _file_hash(),
                'bot_version'   : BOT_VERSION,
                'pnl_today'     : round(float(ov.get('today_pnl', 0)), 4),
                'pnl_total'     : round(float(ov.get('total_pnl', 0)), 4),
                'open_positions': open_pos,
                'total_trades'  : total_trades,
            }, timeout=60)
            if not r.text or not r.text.strip():
                raise Exception("Empty response")
            try:
                d = r.json()
            except ValueError:
                # Server returned something that isn't JSON (HTML error page,
                # proxy timeout page, etc). Log enough to diagnose without
                # spamming the whole page body every 30s.
                raise Exception(f"Non-JSON response (status {r.status_code}): {r.text[:120]!r}")
            valid = d.get('valid', False)
            _license_cache = {
                'valid'     : valid,
                'checked_at': now,
                'expiry'    : d.get('expiry', ''),
                'days'      : d.get('days_remaining', 0),
                'reason'    : d.get('reason', ''),
                'message'   : d.get('message', ''),
            }
            # Update notice — server tells us a newer version has been
            # released. This never auto-downloads or restarts anything —
            # it only shows a persistent "please update" notice on the
            # dashboard and in Telegram until the customer runs the update
            # command themselves. Trading and everything else keeps working
            # normally in the meantime.
            if d.get('update_available') and d.get('latest_version'):
                _pending_update['version'] = d['latest_version']
                _pending_update['whats_new'] = d.get('whats_new', '')
            else:
                _pending_update['version'] = None
                _pending_update['whats_new'] = ''
            save_config_data({
                'license_valid'  : valid,
                'license_expiry' : d.get('expiry', ''),
                'token_status'   : 'active' if valid else 'pending',
            })
            if valid:
                log.info(f"✅ Bot token validated — expires {d.get('expiry','lifetime')}")
            else:
                log.info(f"⏳ Token not yet approved — checking again in 30s")
            return valid
        except Exception as e:
            log.info(f"License check attempt {attempt+1}/2: {e}")
            if attempt == 0:
                time.sleep(10)

    # Grace period — use cached state if server unreachable
    old_valid = cfg.get('license_valid', False)
    _license_cache = {'valid': old_valid, 'checked_at': now, 'reason': 'server_unreachable'}
    return old_valid


def license_activate(token):
    """Validate token with server — token IS the identity now."""
    for attempt in range(3):
        try:
            r = requests.post(f"{LICENSE_SERVER_URL}/validate", json={
                'token'      : token.strip(),
                'device_id'  : _device_id(),
                'device_name': _device_name(),
                'file_hash'  : _file_hash(),
                'pnl_today'  : 0.0,
                'pnl_total'  : 0.0,
                'open_positions': 0,
                'total_trades'  : 0,
            }, timeout=65)
            if not r.text or not r.text.strip():
                raise Exception("Empty response — server waking up")
            return r.json()
        except Exception as e:
            log.info(f"Token validate attempt {attempt+1}/3: {e}")
            if attempt < 2:
                time.sleep(20)
    return {'valid': False, 'message': '⏳ Server is waking up. Please wait 1 minute and try again.'}


def _send_status_report():
    cfg = load_config()
    token   = cfg.get('tg_token','')
    chat_id = cfg.get('tg_chat_id','')
    if not token or not chat_id: return
    try:
        ov = _overall_stats()
        bals = _fetch_balances()
        total_bal = sum(bals.values())
        w = ov['wins']; l = ov['losses']; tot = w+l
        wr = f'{w/tot*100:.0f}%' if tot else '--'
        report = (
            '\U0001f4ca <b>G MAX V1 Status Report</b>\n'
            '\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n'
            f'\U0001f4b0 Balance: <code>${total_bal:.2f}</code>\n'
            f'\U0001f4c8 PnL Today: <code>${ov["today_pnl"]:+.2f}</code>\n'
            f'\U0001f4c8 PnL Total: <code>${ov["total_pnl"]:+.2f}</code>\n'
            f'\U0001f3af Win Rate: {wr} ({w}W/{l}L)\n'
            f'\U0001f4bc Open Positions: {len(state["open_positions"])}\n'
            f'\U0001f4ca Total Trades: {len(state["trades"])}\n'
            f'\U0001f550 {_dt()}'
        )
        requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            data={'chat_id': chat_id, 'text': report, 'parse_mode': 'HTML'},
            timeout=10
        )
        log.info('Status report sent')
    except Exception as e:
        log.info(f'Status report error: {e}')

def _send_data_channel_report():
    """Sends current stats to the license server, which creates (once) or
    edits (every time after) this customer's post in the private data channel.
    Used for backtesting, performance review, and fair billing (don't charge
    a customer who's currently in a loss)."""
    cfg = load_config()
    token = cfg.get('tg_token', '').strip()
    if not token: return
    try:
        ov  = _overall_stats()
        adv = _advanced_stats()
        bals = _fetch_balances()
        total_bal = sum(bals.values())
        open_coins = sorted({display_name(p['symbol']) for p in state['open_positions'].values()})
        closed = [t for t in state['trades'] if t.get('status') == 'closed']
        recent = closed[-10:][::-1]
        history = [{
            'coin'  : display_name(t['symbol']),
            'result': 'tp' if t['pnl'] > 0 else ('sl' if t['pnl'] < 0 else 'be'),
            'pnl'   : round(t['pnl'], 4),
        } for t in recent]

        requests.post(f"{LICENSE_SERVER_URL}/report-stats", json={
            'token'          : token,
            'daily_pnl'      : round(ov.get('today_pnl', 0), 4),
            'total_pnl'      : round(ov.get('total_pnl', 0), 4),
            'total_loss'     : round(sum(t['pnl'] for t in closed if t['pnl'] < 0), 4),
            'total_profit'   : round(sum(t['pnl'] for t in closed if t['pnl'] > 0), 4),
            'winrate'        : round(ov.get('wr', 0), 1),
            'profit_factor'  : (None if ov.get('pf') == float('inf') else round(ov.get('pf', 0), 2)),
            'drawdown'       : round(adv.get('max_drawdown_pct', 0), 2),
            'total_balance'  : round(total_bal, 2),
            'open_positions' : open_coins,
            'trade_history'  : history,
        }, timeout=20)
    except Exception as e:
        log.info(f"data channel report error: {e}")

def license_check_loop():
    time.sleep(5)
    last_report = 0
    last_data_report = 0
    while state.get('running', True):
        try: license_check(force=True)
        except Exception as e: log.info(f'license check error: {e}')
        if time.time() - last_report > 21600:
            try: _send_status_report(); last_report = time.time()
            except Exception as e: log.info(f'report error: {e}')
        if time.time() - last_data_report > 7200:
            try: _send_data_channel_report(); last_data_report = time.time()
            except Exception as e: log.info(f'data report error: {e}')
        cfg2 = load_config()
        if cfg2.get('token_status') == 'active':
            time.sleep(1800)
        else:
            time.sleep(30)


def _fetch_balances():
    totals = {}
    for ex in get_enabled_exchanges():
        cached = state['balance_cache'].get(ex, {})
        if cached and (time.time() - cached.get('ts', 0)) < 60:
            totals[ex] = cached['bal']
            continue
        try:
            key, secret, pp = _get_creds(ex)
            bal = _ex_balance(ex, key, secret, pp)
            state['balance_cache'][ex] = {'bal': bal, 'ts': time.time()}
            totals[ex] = bal
        except Exception as e:
            log.info(f"balance fetch {ex}: {e}")
            totals[ex] = state['balance_cache'].get(ex, {}).get('bal', 0.0)
    return totals

def _total_balance():
    return sum(_fetch_balances().values())

# ── History ────────────────────────────────────────────────
def load_history():
    if HISTORY_PATH.exists():
        try:
            with open(HISTORY_PATH) as f:
                d = json.load(f)
                state['trades']    = d.get('trades', [])
                state['total_pnl'] = d.get('total_pnl', 0.0)
                state['wins']      = d.get('wins', 0)
                state['losses']    = d.get('losses', 0)
        except Exception as e:
            log.info(f"history load failed: {e}")

def save_history():
    try:
        with open(HISTORY_PATH, 'w') as f:
            json.dump({
                'trades'   : state['trades'][-5000:],
                'total_pnl': state['total_pnl'],
                'wins'     : state['wins'],
                'losses'   : state['losses'],
            }, f)
    except Exception as e:
        log.info(f"history save failed: {e}")

# ── Display helpers ────────────────────────────────────────
def display_name(sym):
    return sym.replace('USDT','').replace('/USDT','').replace(':USDT','').replace('1000000','').replace('1000','')

def _today_str():
    return datetime.now().astimezone().strftime('%Y-%m-%d')

# ── Pure Python Exchange Engine (no ccxt / no Rust) ──────
# ── Pure Python Exchange Engine (no ccxt, no Rust) ────────
import hmac, hashlib, base64, urllib.parse

def _ts():
    return str(int(time.time() * 1000))

def _get_creds(ex):
    cfg = load_config()
    key = cfg.get(f'{ex}_key','').strip()
    sec = cfg.get(f'{ex}_secret','').strip()
    pp  = cfg.get(f'{ex}_passphrase','').strip()
    return key, sec, pp

# ── BINANCE FUTURES ──────────────────────────────
def _binance_sign(secret, params):
    qs = urllib.parse.urlencode(params)
    sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return qs + '&signature=' + sig

def _binance_get(path, params, key, secret):
    params['timestamp'] = _ts()
    qs = _binance_sign(secret, params)
    r = requests.get(f'https://fapi.binance.com{path}?{qs}',
                     headers={'X-MBX-APIKEY': key}, timeout=10)
    return r.json()

def _binance_post(path, params, key, secret):
    params['timestamp'] = _ts()
    qs = _binance_sign(secret, params)
    r = requests.post(f'https://fapi.binance.com{path}',
                      data=qs, headers={'X-MBX-APIKEY': key,
                      'Content-Type':'application/x-www-form-urlencoded'}, timeout=10)
    return r.json()

def binance_balance(key, secret):
    d = _binance_get('/fapi/v2/balance', {}, key, secret)
    if isinstance(d, list):
        for asset in d:
            if isinstance(asset, dict) and asset.get('asset') == 'USDT':
                return float(asset.get('availableBalance', 0))
    return 0.0

def binance_price(symbol):
    r = requests.get('https://fapi.binance.com/fapi/v1/ticker/price',
                     params={'symbol': symbol}, timeout=10)
    return float(r.json().get('price', 0))

def binance_listed(symbol):
    r = requests.get('https://fapi.binance.com/fapi/v1/exchangeInfo', timeout=10)
    syms = [s['symbol'] for s in r.json().get('symbols', [])]
    return symbol in syms

def binance_set_leverage(symbol, lev, key, secret):
    try: _binance_post('/fapi/v1/leverage', {'symbol':symbol,'leverage':lev}, key, secret)
    except Exception: pass

def binance_set_margin(symbol, mt, key, secret):
    mtype = 'CROSSED' if mt == 'CROSSED' else 'ISOLATED'
    try: _binance_post('/fapi/v1/marginType', {'symbol':symbol,'marginType':mtype}, key, secret)
    except Exception: pass

def binance_order(symbol, side, qty, key, secret):
    p = {'symbol':symbol,'side':side.upper(),'type':'MARKET','quantity':qty}
    return _binance_post('/fapi/v1/order', p, key, secret)

def binance_tp_sl(symbol, side, qty, tp, sl, key, secret):
    close_side = 'SELL' if side=='buy' else 'BUY'
    # TP
    try:
        res = _binance_post('/fapi/v1/order', {
            'symbol':symbol,'side':close_side,'type':'TAKE_PROFIT_MARKET',
            'stopPrice':str(tp),'closePosition':'true','workingType':'MARK_PRICE'
        }, key, secret)
        if res.get('orderId'):
            log.info(f"✅ Binance TP set: {tp}")
        else:
            log.info(f"⚠️ Binance TP error: {res.get('msg','')}")
    except Exception as e:
        log.info(f"Binance TP exception: {e}")
    # SL
    try:
        res = _binance_post('/fapi/v1/order', {
            'symbol':symbol,'side':close_side,'type':'STOP_MARKET',
            'stopPrice':str(sl),'closePosition':'true','workingType':'MARK_PRICE'
        }, key, secret)
        if res.get('orderId'):
            log.info(f"✅ Binance SL set: {sl}")
        else:
            log.info(f"⚠️ Binance SL error: {res.get('msg','')}")
    except Exception as e:
        log.info(f"Binance SL exception: {e}")

def binance_pos_size(symbol, key, secret):
    """Returns (size, ok). ok=False means the API call failed — caller must NOT
    treat that as 'position closed', or a transient error will falsely unlock the coin."""
    try:
        d = _binance_get('/fapi/v2/positionRisk', {'symbol':symbol}, key, secret)
    except Exception as e:
        log.info(f"Binance pos_size error: {e}")
        return 0.0, False
    if isinstance(d, list):
        for p in d:
            if p.get('symbol') == symbol:
                return abs(float(p.get('positionAmt', 0))), True
        return 0.0, True  # symbol not in list = genuinely no position
    log.info(f"Binance pos_size unexpected response: {str(d)[:150]}")
    return 0.0, False

# ── BYBIT FUTURES ────────────────────────────────
def bybit_balance(key, secret):
    ts = _ts(); recv = '5000'
    qs = 'accountType=UNIFIED'
    pre = ts + key + recv + qs
    sig = hmac.new(secret.encode(), pre.encode(), hashlib.sha256).hexdigest()
    h = {'X-BAPI-API-KEY':key,'X-BAPI-TIMESTAMP':ts,
         'X-BAPI-RECV-WINDOW':recv,'X-BAPI-SIGN':sig}
    r = requests.get('https://api.bybit.com/v5/account/wallet-balance',
                     params={'accountType':'UNIFIED'}, headers=h, timeout=10)
    try:
        coins = r.json()['result']['list'][0]['coin']
        for c in coins:
            if c['coin'] == 'USDT':
                return float(c.get('availableToWithdraw') or c.get('walletBalance') or 0)
    except Exception: pass
    return 0.0

def bybit_price(symbol):
    r = requests.get('https://api.bybit.com/v5/market/tickers',
                     params={'category':'linear','symbol':symbol}, timeout=10)
    try: return float(r.json()['result']['list'][0]['lastPrice'])
    except Exception: return 0.0

def bybit_listed(symbol):
    r = requests.get('https://api.bybit.com/v5/market/instruments-info',
                     params={'category':'linear','symbol':symbol}, timeout=10)
    try: return len(r.json()['result']['list']) > 0
    except Exception: return False

def _bybit_lot_size(symbol):
    """Get minimum lot size and qty step for symbol from Bybit."""
    try:
        r = requests.get('https://api.bybit.com/v5/market/instruments-info',
                         params={'category':'linear','symbol':symbol}, timeout=10)
        info = r.json()['result']['list'][0]
        lot  = info['lotSizeFilter']
        step = float(lot.get('qtyStep','0.001'))
        minq = float(lot.get('minOrderQty','0.001'))
        return step, minq
    except Exception:
        return 0.001, 0.001

def _bybit_round_qty(symbol, qty):
    """Round qty to valid Bybit lot size step."""
    step, minq = _bybit_lot_size(symbol)
    # Round to step precision
    if step >= 1:
        qty = max(minq, round(qty / step) * step)
        return str(int(qty))
    else:
        decimals = len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
        qty = max(minq, round(round(qty / step) * step, decimals))
        return f"{qty:.{decimals}f}"

def bybit_order(symbol, side, qty, key, secret):
    ts = _ts(); recv = '5000'
    qty_str = _bybit_round_qty(symbol, qty)
    payload = {
        'category'   : 'linear',
        'symbol'     : symbol,
        'side'       : side.capitalize(),
        'orderType'  : 'Market',
        'qty'        : qty_str,
        'positionIdx': 0,
    }
    body = json.dumps(payload)
    pre  = ts + key + recv + body
    sig  = hmac.new(secret.encode(), pre.encode(), hashlib.sha256).hexdigest()
    h = {'X-BAPI-API-KEY':key,'X-BAPI-TIMESTAMP':ts,
         'X-BAPI-RECV-WINDOW':recv,'X-BAPI-SIGN':sig,'Content-Type':'application/json'}
    r = requests.post('https://api.bybit.com/v5/order/create',
                      data=body, headers=h, timeout=10)
    return r.json()

def bybit_tp_sl(symbol, side, qty, tp, sl, key, secret):
    """Set TP/SL on Bybit using set-trading-stop — most reliable method."""
    ts = _ts(); recv = '5000'
    payload = {
        'category'   : 'linear',
        'symbol'     : symbol,
        'takeProfit' : str(tp),
        'stopLoss'   : str(sl),
        'tpTriggerBy': 'LastPrice',
        'slTriggerBy': 'LastPrice',
        'positionIdx': 0,
    }
    body = json.dumps(payload)
    pre  = ts + key + recv + body
    sig  = hmac.new(secret.encode(), pre.encode(), hashlib.sha256).hexdigest()
    h = {'X-BAPI-API-KEY':key,'X-BAPI-TIMESTAMP':ts,
         'X-BAPI-RECV-WINDOW':recv,'X-BAPI-SIGN':sig,'Content-Type':'application/json'}
    try:
        r = requests.post('https://api.bybit.com/v5/position/trading-stop',
                          data=body, headers=h, timeout=10)
        res = r.json()
        if res.get('retCode') == 0:
            log.info(f"✅ Bybit TP/SL set: TP={tp} SL={sl}")
        else:
            log.info(f"⚠️ Bybit TP/SL error: {res.get('retMsg','')}")
    except Exception as e:
        log.info(f"Bybit TP/SL exception: {e}")

def bybit_pos_size(symbol, key, secret):
    """Returns (size, ok). ok=False means the API call failed."""
    ts = _ts(); recv = '5000'
    qs = f'category=linear&symbol={symbol}'
    pre = ts + key + recv + qs
    sig = hmac.new(secret.encode(), pre.encode(), hashlib.sha256).hexdigest()
    h = {'X-BAPI-API-KEY':key,'X-BAPI-TIMESTAMP':ts,
         'X-BAPI-RECV-WINDOW':recv,'X-BAPI-SIGN':sig}
    try:
        r = requests.get('https://api.bybit.com/v5/position/list',
                         params={'category':'linear','symbol':symbol}, headers=h, timeout=10)
        data = r.json()
        if data.get('retCode') != 0:
            log.info(f"Bybit pos_size error: {data.get('retMsg','')}")
            return 0.0, False
        for p in data['result']['list']:
            sz = float(p.get('size',0))
            if sz > 0: return sz, True
        return 0.0, True
    except Exception as e:
        log.info(f"Bybit pos_size error: {e}")
        return 0.0, False

# ── BITGET FUTURES ───────────────────────────────
def _bitget_sign(secret, ts, method, path, body=''):
    pre = ts + method.upper() + path + body
    return base64.b64encode(
        hmac.new(secret.encode(), pre.encode(), hashlib.sha256).digest()
    ).decode()

def _bitget_h(key, secret, pp, method, path, body=''):
    ts = _ts()
    sig = _bitget_sign(secret, ts, method, path, body)
    return {'ACCESS-KEY':key,'ACCESS-SIGN':sig,'ACCESS-TIMESTAMP':ts,
            'ACCESS-PASSPHRASE':pp,'Content-Type':'application/json','locale':'en-US'}

def bitget_balance(key, secret, pp):
    path = '/api/v2/mix/account/account?productType=USDT-FUTURES&marginCoin=USDT'
    r = requests.get(f'https://api.bitget.com{path}',
                     headers=_bitget_h(key,secret,pp,'GET',path), timeout=10)
    try: return float(r.json()['data']['available'])
    except Exception: return 0.0

def bitget_price(symbol):
    sym = symbol if symbol.endswith('USDT') else symbol+'USDT'
    r = requests.get('https://api.bitget.com/api/v2/mix/market/ticker',
                     params={'productType':'USDT-FUTURES','symbol':sym}, timeout=10)
    try: return float(r.json()['data'][0]['lastPr'])
    except Exception: return 0.0

def bitget_listed(symbol):
    r = requests.get('https://api.bitget.com/api/v2/mix/market/contracts',
                     params={'productType':'USDT-FUTURES'}, timeout=10)
    try:
        sym = symbol if symbol.endswith('USDT') else symbol+'USDT'
        syms = [s['symbol'] for s in r.json().get('data',[])]
        return sym in syms
    except Exception: return False

def bitget_order(symbol, side, qty, key, secret, pp, mt='CROSSED'):
    path = '/api/v2/mix/order/place-order'
    sym = symbol if symbol.endswith('USDT') else symbol+'USDT'
    margin_mode = 'crossed' if mt == 'CROSSED' else 'isolated'
    # Bitget V2: side = 'buy'/'sell', tradeSide = 'open'
    body = json.dumps({'symbol':sym,'productType':'USDT-FUTURES','marginMode':margin_mode,
                       'marginCoin':'USDT','size':str(round(qty,3)),
                       'side':'buy' if side=='buy' else 'sell',
                       'tradeSide':'open','orderType':'market'})
    r = requests.post(f'https://api.bitget.com{path}', data=body,
                      headers=_bitget_h(key,secret,pp,'POST',path,body), timeout=10)
    return r.json()

def bitget_pos_size(symbol, key, secret, pp):
    """Returns (size, ok). ok=False means the API call failed."""
    sym = symbol if symbol.endswith('USDT') else symbol+'USDT'
    path = f'/api/v2/mix/position/single-position?productType=USDT-FUTURES&symbol={sym}&marginCoin=USDT'
    try:
        r = requests.get(f'https://api.bitget.com{path}',
                         headers=_bitget_h(key,secret,pp,'GET',path), timeout=10)
        data = r.json()
        if data.get('code') != '00000':
            log.info(f"Bitget pos_size error: {data.get('msg','')}")
            return 0.0, False
        for p in data.get('data',[]):
            sz = float(p.get('total',0))
            if sz > 0: return sz, True
        return 0.0, True
    except Exception as e:
        log.info(f"Bitget pos_size error: {e}")
        return 0.0, False

# ── OKX FUTURES ─────────────────────────────────
def _okx_sign(secret, ts, method, path, body=''):
    pre = ts + method.upper() + path + body
    return base64.b64encode(
        hmac.new(secret.encode(), pre.encode(), hashlib.sha256).digest()
    ).decode()

def _okx_h(key, secret, pp, method, path, body=''):
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.000Z')
    sig = _okx_sign(secret, ts, method, path, body)
    return {'OK-ACCESS-KEY':key,'OK-ACCESS-SIGN':sig,'OK-ACCESS-TIMESTAMP':ts,
            'OK-ACCESS-PASSPHRASE':pp,'Content-Type':'application/json'}

def okx_balance(key, secret, pp):
    path = '/api/v5/account/balance?ccy=USDT'
    r = requests.get(f'https://www.okx.com{path}',
                     headers=_okx_h(key,secret,pp,'GET',path), timeout=10)
    try:
        for d in r.json()['data'][0]['details']:
            if d['ccy'] == 'USDT':
                return float(d.get('availEq') or d.get('eq') or 0)
    except Exception: pass
    return 0.0

def okx_price(symbol):
    inst = symbol.replace('USDT','') + '-USDT-SWAP'
    r = requests.get('https://www.okx.com/api/v5/market/ticker',
                     params={'instId':inst}, timeout=10)
    try: return float(r.json()['data'][0]['last'])
    except Exception: return 0.0

def okx_listed(symbol):
    inst = symbol.replace('USDT','') + '-USDT-SWAP'
    r = requests.get('https://www.okx.com/api/v5/public/instruments',
                     params={'instType':'SWAP','instId':inst}, timeout=10)
    try: return len(r.json().get('data',[])) > 0
    except Exception: return False

def okx_order(symbol, side, qty, key, secret, pp, mt='CROSSED'):
    inst = symbol.replace('USDT','') + '-USDT-SWAP'
    td_mode = 'cross' if mt == 'CROSSED' else 'isolated'
    # OKX sz = number of contracts (1 contract = 1 unit for most coins)
    sz = str(max(1, int(qty)))
    body = json.dumps({'instId':inst,'tdMode':td_mode,'side':side,
                       'ordType':'market','sz':sz,
                       'posSide':'long' if side=='buy' else 'short'})
    path = '/api/v5/trade/order'
    r = requests.post(f'https://www.okx.com{path}', data=body,
                      headers=_okx_h(key,secret,pp,'POST',path,body), timeout=10)
    return r.json()

def okx_pos_size(symbol, key, secret, pp):
    """Returns (size, ok). ok=False means the API call failed."""
    inst = symbol.replace('USDT','') + '-USDT-SWAP'
    path = f'/api/v5/account/positions?instId={inst}'
    try:
        r = requests.get(f'https://www.okx.com{path}',
                         headers=_okx_h(key,secret,pp,'GET',path), timeout=10)
        data = r.json()
        if data.get('code') != '0':
            log.info(f"OKX pos_size error: {data.get('msg','')}")
            return 0.0, False
        for p in data.get('data',[]):
            sz = float(p.get('pos',0))
            if sz != 0: return abs(sz), True
        return 0.0, True
    except Exception as e:
        log.info(f"OKX pos_size error: {e}")
        return 0.0, False

# ── KUCOIN FUTURES ───────────────────────────────
def _kucoin_sign(secret, ts, method, path, body=''):
    pre = ts + method.upper() + path + body
    return base64.b64encode(
        hmac.new(secret.encode(), pre.encode(), hashlib.sha256).digest()
    ).decode()

def _kucoin_h(key, secret, pp, method, path, body=''):
    ts = _ts()
    sig = _kucoin_sign(secret, ts, method, path, body)
    pp_sig = base64.b64encode(
        hmac.new('2'.encode(), pp.encode(), hashlib.sha256).digest()
    ).decode() if pp else pp
    return {'KC-API-KEY':key,'KC-API-SIGN':sig,'KC-API-TIMESTAMP':ts,
            'KC-API-PASSPHRASE':pp_sig,'KC-API-KEY-VERSION':'2',
            'Content-Type':'application/json'}

def kucoin_balance(key, secret, pp):
    path = '/api/v1/account-overview?currency=USDT'
    r = requests.get(f'https://api-futures.kucoin.com{path}',
                     headers=_kucoin_h(key,secret,pp,'GET',path), timeout=10)
    try: return float(r.json()['data'].get('availableBalance',0))
    except Exception: return 0.0

def kucoin_price(symbol):
    sym = symbol.replace('USDT','USDTM')
    r = requests.get('https://api-futures.kucoin.com/api/v1/ticker',
                     params={'symbol':sym}, timeout=10)
    try: return float(r.json()['data']['price'])
    except Exception: return 0.0

def kucoin_listed(symbol):
    r = requests.get('https://api-futures.kucoin.com/api/v1/contracts/active', timeout=10)
    try:
        syms = [s['symbol'] for s in r.json().get('data',[])]
        return symbol.replace('USDT','USDTM') in syms
    except Exception: return False

def kucoin_order(symbol, side, qty, key, secret, pp):
    sym = symbol.replace('USDT','USDTM')
    path = '/api/v1/orders'
    body = json.dumps({'clientOid':_ts(),'side':side,'symbol':sym,
                       'type':'market','size':max(1,int(qty)),'leverage':'5'})
    r = requests.post(f'https://api-futures.kucoin.com{path}', data=body,
                      headers=_kucoin_h(key,secret,pp,'POST',path,body), timeout=10)
    return r.json()

def kucoin_pos_size(symbol, key, secret, pp):
    """Returns (size, ok). ok=False means the API call failed."""
    sym = symbol.replace('USDT','USDTM')
    path = f'/api/v1/position?symbol={sym}'
    try:
        r = requests.get(f'https://api-futures.kucoin.com{path}',
                         headers=_kucoin_h(key,secret,pp,'GET',path), timeout=10)
        data = r.json()
        if data.get('code') != '200000':
            log.info(f"KuCoin pos_size error: {data.get('msg','')}")
            return 0.0, False
        return abs(float(data.get('data',{}).get('currentQty',0))), True
    except Exception as e:
        log.info(f"KuCoin pos_size error: {e}")
        return 0.0, False

def _bitget_tp_sl(symbol, side, qty, tp, sl, key, secret, pp, mt='CROSSED'):
    """Place TP + SL orders on Bitget futures."""
    sym = symbol if symbol.endswith('USDT') else symbol+'USDT'
    close_side = 'sell' if side=='buy' else 'buy'
    margin_mode = 'crossed' if mt == 'CROSSED' else 'isolated'
    # TP limit order
    try:
        path = '/api/v2/mix/order/place-order'
        body = json.dumps({
            'symbol':sym,'productType':'USDT-FUTURES','marginMode':margin_mode,
            'marginCoin':'USDT','size':str(qty),'side':close_side,
            'tradeSide':'close','orderType':'limit','price':str(tp),
        })
        requests.post(f'https://api.bitget.com{path}', data=body,
                      headers=_bitget_h(key,secret,pp,'POST',path,body), timeout=10)
    except Exception as e:
        log.info(f"Bitget TP error: {e}")
    # SL stop order
    try:
        path = '/api/v2/mix/order/place-tpsl-order'
        body = json.dumps({
            'symbol':sym,'productType':'USDT-FUTURES','marginCoin':'USDT',
            'planType':'loss_plan','triggerPrice':str(sl),'executePrice':str(sl),
            'size':str(qty),'side':close_side,'tradeSide':'close',
        })
        requests.post(f'https://api.bitget.com{path}', data=body,
                      headers=_bitget_h(key,secret,pp,'POST',path,body), timeout=10)
    except Exception as e:
        log.info(f"Bitget SL error: {e}")

def _okx_tp_sl(symbol, side, qty, tp, sl, key, secret, pp, mt='CROSSED'):
    """Place TP + SL orders on OKX futures."""
    inst = symbol.replace('USDT','') + '-USDT-SWAP'
    close_side = 'sell' if side=='buy' else 'buy'
    pos_side   = 'long' if side=='buy' else 'short'
    td_mode    = 'cross' if mt == 'CROSSED' else 'isolated'
    path = '/api/v5/trade/order-algo'
    # TP
    try:
        body = json.dumps({
            'instId':inst,'tdMode':td_mode,'side':close_side,'posSide':pos_side,
            'ordType':'conditional','sz':str(max(1,int(qty))),
            'tpTriggerPx':str(tp),'tpOrdPx':str(tp),
        })
        requests.post(f'https://www.okx.com{path}', data=body,
                      headers=_okx_h(key,secret,pp,'POST',path,body), timeout=10)
    except Exception as e:
        log.info(f"OKX TP error: {e}")
    # SL
    try:
        body = json.dumps({
            'instId':inst,'tdMode':td_mode,'side':close_side,'posSide':pos_side,
            'ordType':'conditional','sz':str(max(1,int(qty))),
            'slTriggerPx':str(sl),'slOrdPx':'-1',
        })
        requests.post(f'https://www.okx.com{path}', data=body,
                      headers=_okx_h(key,secret,pp,'POST',path,body), timeout=10)
    except Exception as e:
        log.info(f"OKX SL error: {e}")

def _kucoin_tp_sl(symbol, side, qty, tp, sl, key, secret, pp):
    """Place TP + SL orders on KuCoin futures."""
    sym = symbol.replace('USDT','USDTM')
    close_side = 'sell' if side=='buy' else 'buy'
    path = '/api/v1/orders'
    # TP limit
    try:
        body = json.dumps({
            'clientOid': _ts()+'tp', 'side':close_side, 'symbol':sym,
            'type':'limit', 'size':max(1,int(qty)),
            'price':str(tp), 'reduceOnly':True,
        })
        requests.post(f'https://api-futures.kucoin.com{path}', data=body,
                      headers=_kucoin_h(key,secret,pp,'POST',path,body), timeout=10)
    except Exception as e:
        log.info(f"KuCoin TP error: {e}")
    # SL stop
    try:
        body = json.dumps({
            'clientOid': _ts()+'sl', 'side':close_side, 'symbol':sym,
            'type':'market', 'size':max(1,int(qty)),
            'stop': 'down' if side=='buy' else 'up',
            'stopPrice':str(sl), 'stopPriceType':'TP',
            'reduceOnly':True,
        })
        requests.post(f'https://api-futures.kucoin.com/api/v1/st-orders', data=body,
                      headers=_kucoin_h(key,secret,pp,'POST',
                      '/api/v1/st-orders',body), timeout=10)
    except Exception as e:
        log.info(f"KuCoin SL error: {e}")

# ── Leverage / margin-mode setters ───────────────
def _bybit_set_margin_mode(symbol, mt, key, secret):
    """mt: 'CROSSED' or 'ISOLATED'. Bybit switch-isolated: 0=cross-margin, 1=isolated-margin."""
    try:
        trade_mode = 0 if mt == 'CROSSED' else 1
        ts = _ts(); recv = '5000'
        body = json.dumps({'category':'linear','symbol':symbol,
                           'tradeMode':trade_mode,
                           'buyLeverage':'20', 'sellLeverage':'20'})
        pre  = ts + key + recv + body
        sig  = hmac.new(secret.encode(), pre.encode(), hashlib.sha256).hexdigest()
        h = {'X-BAPI-API-KEY':key,'X-BAPI-TIMESTAMP':ts,
             'X-BAPI-RECV-WINDOW':recv,'X-BAPI-SIGN':sig,'Content-Type':'application/json'}
        r = requests.post('https://api.bybit.com/v5/position/switch-isolated',
                          data=body, headers=h, timeout=10)
        res = r.json()
        # retCode 0 = success, 110026 = "already in this mode" — both are fine, not real errors
        if res.get('retCode') not in (0, 110026):
            log.info(f"Bybit set margin mode: {res.get('retMsg','')}")
    except Exception as e:
        log.info(f"Bybit set margin mode: {e}")

def _bybit_set_leverage(symbol, lev, key, secret):
    try:
        ts = _ts(); recv = '5000'
        body = json.dumps({'category':'linear','symbol':symbol,
                           'buyLeverage':str(lev),'sellLeverage':str(lev)})
        pre  = ts + key + recv + body
        sig  = hmac.new(secret.encode(), pre.encode(), hashlib.sha256).hexdigest()
        h = {'X-BAPI-API-KEY':key,'X-BAPI-TIMESTAMP':ts,
             'X-BAPI-RECV-WINDOW':recv,'X-BAPI-SIGN':sig,'Content-Type':'application/json'}
        requests.post('https://api.bybit.com/v5/position/set-leverage',
                      data=body, headers=h, timeout=10)
    except Exception as e:
        log.info(f"Bybit set leverage: {e}")

def _bitget_set_margin_mode(symbol, mt, key, secret, pp):
    """mt: 'CROSSED' or 'ISOLATED'."""
    try:
        path = '/api/v2/mix/account/set-margin-mode'
        sym  = symbol if symbol.endswith('USDT') else symbol + 'USDT'
        mode = 'crossed' if mt == 'CROSSED' else 'isolated'
        body = json.dumps({'symbol':sym,'productType':'USDT-FUTURES',
                           'marginCoin':'USDT','marginMode':mode})
        requests.post(f'https://api.bitget.com{path}', data=body,
                      headers=_bitget_h(key,secret,pp,'POST',path,body), timeout=10)
    except Exception as e:
        log.info(f"Bitget set margin mode: {e}")

def _bitget_set_leverage(symbol, lev, key, secret, pp):
    try:
        path = '/api/v2/mix/account/set-leverage'
        sym  = symbol if symbol.endswith('USDT') else symbol + 'USDT'
        body = json.dumps({'symbol':sym,'productType':'USDT-FUTURES',
                           'marginCoin':'USDT','leverage':str(lev)})
        requests.post(f'https://api.bitget.com{path}', data=body,
                      headers=_bitget_h(key,secret,pp,'POST',path,body), timeout=10)
    except Exception as e:
        log.info(f"Bitget set leverage: {e}")

def _okx_set_leverage(symbol, lev, key, secret, pp, mt='CROSSED'):
    try:
        inst = symbol.replace('USDT','') + '-USDT-SWAP'
        mgn_mode = 'cross' if mt == 'CROSSED' else 'isolated'
        body = json.dumps({'instId':inst,'lever':str(lev),'mgnMode':mgn_mode})
        path = '/api/v5/account/set-leverage'
        requests.post(f'https://www.okx.com{path}', data=body,
                      headers=_okx_h(key,secret,pp,'POST',path,body), timeout=10)
    except Exception as e:
        log.info(f"OKX set leverage: {e}")

def _kucoin_set_margin_mode(symbol, mt, key, secret, pp):
    """mt: 'CROSSED' or 'ISOLATED'. KuCoin position-mode: 'CROSS' or 'ISOLATED'."""
    try:
        sym = symbol.replace('USDT','USDTM')
        path = '/api/v2/position/changeMarginMode'
        mode = 'CROSS' if mt == 'CROSSED' else 'ISOLATED'
        body = json.dumps({'symbol':sym,'marginMode':mode})
        requests.post(f'https://api-futures.kucoin.com{path}', data=body,
                      headers=_kucoin_h(key,secret,pp,'POST',path,body), timeout=10)
    except Exception as e:
        log.info(f"KuCoin set margin mode: {e}")

# ── Unified caller ───────────────────────────────
def _ex_balance(ex, key, secret, pp):
    if ex=='binance': return binance_balance(key,secret)
    if ex=='bybit':   return bybit_balance(key,secret)
    if ex=='bitget':  return bitget_balance(key,secret,pp)
    if ex=='okx':     return okx_balance(key,secret,pp)
    if ex=='kucoin':  return kucoin_balance(key,secret,pp)
    return 0.0

def _ex_price(ex, symbol):
    if ex=='binance': return binance_price(symbol)
    if ex=='bybit':   return bybit_price(symbol)
    if ex=='bitget':  return bitget_price(symbol)
    if ex=='okx':     return okx_price(symbol)
    if ex=='kucoin':  return kucoin_price(symbol)
    return 0.0

def _ex_listed(ex, symbol):
    if ex=='binance': return binance_listed(symbol)
    if ex=='bybit':   return bybit_listed(symbol)
    if ex=='bitget':  return bitget_listed(symbol)
    if ex=='okx':     return okx_listed(symbol)
    if ex=='kucoin':  return kucoin_listed(symbol)
    return False

def _ex_pos_size(ex, symbol, key, secret, pp):
    """Returns (size, ok). ok=False means the exchange call failed and the size
    is NOT trustworthy — caller must not treat it as 'position closed'."""
    if ex=='binance': return binance_pos_size(symbol,key,secret)
    if ex=='bybit':   return bybit_pos_size(symbol,key,secret)
    if ex=='bitget':  return bitget_pos_size(symbol,key,secret,pp)
    if ex=='okx':     return okx_pos_size(symbol,key,secret,pp)
    if ex=='kucoin':  return kucoin_pos_size(symbol,key,secret,pp)
    return 0.0, False


# ── Position lock check ──────────────────────────────────
def is_coin_locked(ex, symbol):
    key = f"{ex}:{symbol}"
    return key in state['open_positions']

def load_open_positions():
    """Restores the open-position lock table from disk on startup, so a
    restart (crash, phone reboot, update) never forgets a real open
    position and lets a duplicate signal stack on top of it. This file
    is the primary safety net; _reconcile_open_positions() below is a
    second, independent check against the exchange itself."""
    if POSITIONS_PATH.exists():
        try:
            with open(POSITIONS_PATH) as f:
                d = json.load(f)
                state['open_positions'] = d.get('open_positions', {})
                log.info(f"Restored {len(state['open_positions'])} open position lock(s) from disk")
        except Exception as e:
            log.info(f"open_positions load failed: {e}")

def save_open_positions():
    try:
        with open(POSITIONS_PATH, 'w') as f:
            json.dump({'open_positions': state['open_positions']}, f)
    except Exception as e:
        log.info(f"open_positions save failed: {e}")

def _reconcile_open_positions():
    """Runs once at startup, after load_open_positions(). Checks every
    locked coin against the real exchange balance: if the exchange says
    the position is actually flat (closed while the bot was offline —
    TP/SL hit, manual close, liquidation), the lock is released so a
    real new signal isn't blocked forever on a position that no longer
    exists. If the exchange confirms the position is still open, or the
    check fails/can't be verified, the lock is kept — erring toward
    "still locked" is always the safer default here."""
    if not state['open_positions']:
        return
    log.info(f"Reconciling {len(state['open_positions'])} restored lock(s) against exchange...")
    for key in list(state['open_positions'].keys()):
        try:
            ex, symbol = key.split(':', 1)
            k, sec, pp = _get_creds(ex)
            if not k:
                continue  # no creds for this exchange anymore — leave locked, safest default
            sz, ok = _ex_pos_size(ex, symbol, k, sec, pp)
            if not ok:
                log.info(f"  {key}: exchange check failed — keeping lock (safe default)")
                continue
            if sz == 0:
                log.info(f"  {key}: exchange confirms flat — position closed while bot was offline, releasing lock")
                pos = state['open_positions'].pop(key, None)
                if pos:
                    trade = {**pos, 'status':'closed', 'exit':pos.get('entry',0),
                             'pnl':0.0, 'closed':_dt(), 'closed_date':_today_str(),
                             'reason':'closed_while_offline'}
                    state['trades'].append(trade)
                    save_history()
            else:
                log.info(f"  {key}: exchange confirms still open — lock kept")
        except Exception as e:
            log.info(f"  {key}: reconcile error ({e}) — keeping lock (safe default)")
    save_open_positions()

def _record_open(ex, symbol, side, qty, entry, tp, sl, leverage):
    key = f"{ex}:{symbol}"
    state['open_positions'][key] = {
        'exchange': ex, 'symbol': symbol, 'side': side,
        'qty': qty, 'entry': entry, 'tp': tp, 'sl': sl,
        'leverage': leverage, 'opened': _dt(), 'opened_ts': time.time(),
    }
    save_open_positions()

def _record_close(ex, symbol, exit_price, reason):
    key = f"{ex}:{symbol}"
    pos = state['open_positions'].pop(key, None)
    if not pos: return
    if pos['side'] == 'buy':
        pnl = (exit_price - pos['entry']) * pos['qty']
    else:
        pnl = (pos['entry'] - exit_price) * pos['qty']
    trade = {**pos, 'status':'closed', 'exit':exit_price,
             'pnl':round(pnl,4), 'closed':_dt(), 'closed_date':_today_str(), 'reason':reason}
    state['trades'].append(trade)
    state['total_pnl'] += pnl
    if pnl > 0: state['wins'] += 1
    elif pnl < 0: state['losses'] += 1
    save_history()
    save_open_positions()
    log.info(f"CLOSED {ex}:{symbol} pnl={round(pnl,4)} reason={reason}")

def place_order_on_exchange(ex, symbol, side, tp, sl, leverage, margin_arg):
    """Pure-requests futures order. Returns (success, message)."""
    key, secret, pp = _get_creds(ex)
    if not key or not secret:
        return False, f"No credentials for {EXCHANGE_LABELS[ex]}"

    # Check listed
    try:
        if not _ex_listed(ex, symbol):
            msg = f"⚠️ {display_name(symbol)} not listed on {EXCHANGE_LABELS[ex]} Futures"
            log.info(msg); state['exchange_status'][ex] = 'error'; _send_tg_admin(msg)
            return False, msg
    except Exception as e:
        log.info(f"listed check {ex}: {e}")

    try:
        s = get_settings()
        bal = _ex_balance(ex, key, secret, pp)
        if s['margin_mode'] == 'percent':
            margin = bal * s['margin_percent'] / 100.0
        else:
            margin = s['margin_usd']

        # Set leverage + margin mode (cross/isolated) for all exchanges
        # Margin mode MUST be set before leverage on Bybit/OKX or the API rejects it,
        # and before the order call on Bitget/KuCoin since it can't change with an open position.
        lev = s['leverage']
        mt  = s['margin_type']  # 'CROSSED' or 'ISOLATED'
        if ex == 'binance':
            binance_set_margin(symbol, mt, key, secret)
            binance_set_leverage(symbol, lev, key, secret)
        elif ex == 'bybit':
            _bybit_set_margin_mode(symbol, mt, key, secret)
            _bybit_set_leverage(symbol, lev, key, secret)
        elif ex == 'bitget':
            _bitget_set_margin_mode(symbol, mt, key, secret, pp)
            _bitget_set_leverage(symbol, lev, key, secret, pp)
        elif ex == 'okx':
            _okx_set_leverage(symbol, lev, key, secret, pp, mt)
        elif ex == 'kucoin':
            _kucoin_set_margin_mode(symbol, mt, key, secret, pp)

        price = _ex_price(ex, symbol)
        if price <= 0: return False, f"Could not get price on {EXCHANGE_LABELS[ex]}"

        notional = margin * s['leverage']
        qty = round(notional / price, 3)
        if qty <= 0: return False, f"Calculated qty=0 on {EXCHANGE_LABELS[ex]}"

        # Insufficient margin check
        if s["margin_mode"] == "fixed" and bal < margin:
            msg = (
                f"\u26a0\ufe0f <b>Insufficient Margin</b>\n"
                f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                f"Exchange: {EXCHANGE_LABELS[ex]}\n"
                f"Required: <code>${margin:.2f}</code>\n"
                f"Available: <code>${bal:.2f}</code>\n"
                f"Signal {display_name(symbol)} skipped on {EXCHANGE_LABELS[ex]}"
            )
            log.info(msg)
            _send_tg_admin(msg)
            return False, msg


        # Place order — retry once if rate limited
        res = None
        for _attempt in range(2):
            if ex == 'binance':   res = binance_order(symbol, side, qty, key, secret)
            elif ex == 'bybit':   res = bybit_order(symbol, side, qty, key, secret)
            elif ex == 'bitget':  res = bitget_order(symbol, side, qty, key, secret, pp, mt)
            elif ex == 'okx':     res = okx_order(symbol, side, qty, key, secret, pp, mt)
            elif ex == 'kucoin':  res = kucoin_order(symbol, side, qty, key, secret, pp)

            log.info(f"Order response {ex} (attempt {_attempt+1}): {str(res)[:200]}")

            # Check if rate limited — wait and retry
            rate_limited = False
            if ex == 'bybit' and res:
                if 'too many visits' in str(res.get('retMsg','')).lower() or res.get('retCode') == 10006:
                    rate_limited = True
            elif ex == 'binance' and res:
                if res.get('code') in (-1003, -1015):
                    rate_limited = True
            if rate_limited and _attempt == 0:
                log.info(f"Rate limited on {ex} — waiting 5s then retry")
                time.sleep(5)
                continue
            break

        # ── Validate order response per exchange ──────────
        order_ok = False
        err_msg  = ''
        if ex == 'binance':
            # Success: has 'orderId'
            if res and res.get('orderId'):
                order_ok = True
            else:
                err_msg = res.get('msg', str(res)[:100]) if res else 'No response'

        elif ex == 'bybit':
            # Success: retCode == 0
            if res and res.get('retCode') == 0:
                order_ok = True
            else:
                err_msg = res.get('retMsg', str(res)[:100]) if res else 'No response'

        elif ex == 'bitget':
            # Success: code == '00000'
            if res and res.get('code') == '00000':
                order_ok = True
            else:
                err_msg = res.get('msg', str(res)[:100]) if res else 'No response'

        elif ex == 'okx':
            # Success: data[0].sCode == '0'
            try:
                if res and res.get('data') and res['data'][0].get('sCode') == '0':
                    order_ok = True
                else:
                    err_msg = res['data'][0].get('sMsg','') if res and res.get('data') else str(res)[:100]
            except Exception:
                err_msg = str(res)[:100]

        elif ex == 'kucoin':
            # Success: code == '200000'
            if res and res.get('code') == '200000':
                order_ok = True
            else:
                err_msg = res.get('msg', str(res)[:100]) if res else 'No response'

        if not order_ok:
            state['exchange_status'][ex] = 'error'
            msg = f"❌ {EXCHANGE_LABELS[ex]} order rejected: {err_msg}"
            log.info(msg)
            _send_tg_admin(msg)
            return False, msg

        # ── Order confirmed — place TP/SL for ALL exchanges ──
        try:
            if ex == 'binance':
                binance_tp_sl(symbol, side, qty, tp, sl, key, secret)
            elif ex == 'bybit':
                bybit_tp_sl(symbol, side, qty, tp, sl, key, secret)
            elif ex == 'bitget':
                _bitget_tp_sl(symbol, side, qty, tp, sl, key, secret, pp, mt)
            elif ex == 'okx':
                _okx_tp_sl(symbol, side, qty, tp, sl, key, secret, pp, mt)
            elif ex == 'kucoin':
                _kucoin_tp_sl(symbol, side, qty, tp, sl, key, secret, pp)
            log.info(f"✅ TP/SL placed for {ex}: TP={tp} SL={sl}")
        except Exception as e:
            log.info(f"⚠️ TP/SL placement error {ex}: {e}")

        _record_open(ex, symbol, side, qty, price, tp, sl, s['leverage'])
        state['exchange_status'][ex] = 'ok'
        icon = '🟢' if side == 'buy' else '🔴'
        direction = 'LONG/BUY' if side == 'buy' else 'SHORT/SELL'
        msg = (
            f"{icon} <b>Trade Opened</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💎 <b>{display_name(symbol)}/USDT</b> [{EXCHANGE_LABELS[ex]}]\n"
            f"{icon} {direction}\n\n"
            f"Entry: <code>{price:.6g}</code>\n"
            f"Target: <code>{tp}</code>\n"
            f"Stop Loss: <code>{sl}</code>\n"
            f"Leverage: {s['leverage']}x | Qty: {qty}\n"
            f"Margin: <code>${margin:.2f}</code>"
        )
        log.info(f"Trade opened: {display_name(symbol)} {direction} @ {price:.6g}")
        _send_tg_admin(msg)
        return True, msg

    except Exception as e:
        err = str(e)[:120]
        state['exchange_status'][ex] = 'error'
        msg = f"❌ {EXCHANGE_LABELS[ex]} order failed: {err}"
        log.info(msg); _send_tg_admin(msg)
        return False, msg

def _monitor_positions():
    """Check TP/SL hits and enforce 10-day max hold time."""
    for key in list(state['open_positions'].keys()):
        pos = state['open_positions'].get(key)
        if not pos: continue
        ex = pos['exchange']; symbol = pos['symbol']
        try:
            k, sec, pp = _get_creds(ex)
            if not k: continue

            # ── 10-day auto-close ──────────────────────────
            hold_seconds = time.time() - pos.get('opened_ts', time.time())
            if hold_seconds >= 10 * 24 * 3600:
                log.info(f"⏰ 10-day limit hit for {key} — force closing")
                exit_price = _ex_price(ex, symbol)
                try:
                    close_side = 'sell' if pos['side']=='buy' else 'buy'
                    if ex == 'binance':   binance_order(symbol, close_side, pos['qty'], k, sec)
                    elif ex == 'bybit':   bybit_order(symbol, close_side, pos['qty'], k, sec)
                    elif ex == 'bitget':  bitget_order(symbol, close_side, pos['qty'], k, sec, pp)
                    elif ex == 'okx':     okx_order(symbol, close_side, pos['qty'], k, sec, pp)
                    elif ex == 'kucoin':  kucoin_order(symbol, close_side, pos['qty'], k, sec, pp)
                except Exception as ce:
                    log.info(f"force close order error {key}: {ce}")
                pnl_est = (exit_price - pos['entry']) * pos['qty'] if pos['side']=='buy' else (pos['entry'] - exit_price) * pos['qty']
                _record_close(ex, symbol, exit_price, '10day_auto_close')
                _send_tg_admin(
                    f"⏰ <b>10-Day Auto-Close</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"💎 {display_name(symbol)} [{EXCHANGE_LABELS[ex]}]\n"
                    f"Exit @ <code>{exit_price:.6g}</code>\n"
                    f"Est PnL: <code>${pnl_est:+.4f}</code>"
                )
                continue

            # ── TP/SL hit detection ────────────────────────
            sz, ok = _ex_pos_size(ex, symbol, k, sec, pp)
            if not ok:
                # API call failed (timeout, auth hiccup, rate limit, bad response).
                # We do NOT know the real position state — do NOT treat this as
                # "closed", or a transient error will falsely unlock the coin and
                # let a later signal stack a second position on top of a live one.
                pos['pos_check_fail_count'] = pos.get('pos_check_fail_count', 0) + 1
                if pos['pos_check_fail_count'] % 10 == 0:
                    log.info(f"⚠️ {key}: position size check failing repeatedly "
                             f"({pos['pos_check_fail_count']}x) — still treating as OPEN/locked")
                continue
            pos['pos_check_fail_count'] = 0

            if sz == 0:
                # Require two consecutive confirmed-flat reads (~60s apart) before
                # declaring the position closed, to absorb any exchange-side
                # propagation delay right after TP/SL fires.
                if not pos.get('flat_confirmed'):
                    pos['flat_confirmed'] = True
                    continue

                exit_price = _ex_price(ex, symbol)
                # Determine if TP or SL was hit
                if pos['side'] == 'buy':
                    reason = 'tp_hit' if exit_price >= pos['tp'] * 0.99 else 'sl_hit'
                else:
                    reason = 'tp_hit' if exit_price <= pos['tp'] * 1.01 else 'sl_hit'
                pnl_est = (exit_price - pos['entry']) * pos['qty'] if pos['side']=='buy' else (pos['entry'] - exit_price) * pos['qty']
                _record_close(ex, symbol, exit_price, reason)

                icon  = '✅' if reason == 'tp_hit' else '❌'
                label = 'TAKE PROFIT HIT 🎯' if reason == 'tp_hit' else 'STOP LOSS HIT 🛑'
                _send_tg_admin(
                    f"{icon} <b>{label}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"💎 <b>{display_name(symbol)}/USDT</b> [{EXCHANGE_LABELS[ex]}]\n"
                    f"{'🟢 LONG' if pos['side']=='buy' else '🔴 SHORT'}\n"
                    f"Entry: <code>{pos['entry']:.6g}</code>\n"
                    f"Exit:  <code>{exit_price:.6g}</code>\n"
                    f"Est PnL: <code>${pnl_est:+.4f}</code>"
                )
            else:
                pos['flat_confirmed'] = False
        except Exception as e:
            log.info(f"monitor {key}: {e}")


# ── Signal parser ──────────────────────────────────────────
def parse_signal(text):
    """
    Parse signal messages from the channel. Example:
      🟢 Long/Buy
      HEMI/USDT

      Entry Point - 0.004975
      Targets: 0.005124
      Leverage - 5x
      Stop Loss - 0.004229
    Returns dict or None.
    """
    if not text: return None
    text = text.strip()

    # Direction
    if '🟢' in text or 'Long/Buy' in text or 'long/buy' in text.lower():
        side = 'buy'
    elif '🔴' in text or 'Short/Sell' in text or 'short/sell' in text.lower():
        side = 'sell'
    else:
        return None

    # Symbol — line containing USDT
    sym_match = re.search(r'([A-Z0-9]{2,20}/USDT|[A-Z0-9]{2,20}USDT)', text)
    if not sym_match: return None
    raw_sym = sym_match.group(1).replace('/','')  # normalise to HEMIUSDT

    # Entry
    ep_match = re.search(r'Entry\s+Point\s*[-–:]\s*([\d.]+)', text, re.IGNORECASE)
    if not ep_match: return None
    entry = float(ep_match.group(1))

    # Targets / TP
    tp_match = re.search(r'Targets?\s*[:–-]?\s*([\d.]+)', text, re.IGNORECASE)
    if not tp_match: return None
    tp = float(tp_match.group(1))

    # Stop Loss
    sl_match = re.search(r'Stop\s+Loss\s*[-–:]\s*([\d.]+)', text, re.IGNORECASE)
    if not sl_match: return None
    sl = float(sl_match.group(1))

    # Leverage (default 5 if not found)
    lev_match = re.search(r'Leverage\s*[-–:]\s*(\d+)x', text, re.IGNORECASE)
    lev = int(lev_match.group(1)) if lev_match else 5

    return {'symbol': raw_sym, 'side': side, 'entry': entry, 'tp': tp, 'sl': sl, 'leverage': lev}

# ── Process a confirmed signal ─────────────────────────────
def process_signal(sig):
    # Hard gate: don't trade unless the license is currently valid. Without
    # this, a customer's bot would happily open real trades on their exchange
    # even while showing "Awaiting Approval" on the dashboard — the message
    # was purely cosmetic and never actually stopped execution.
    cfg_lic = load_config()
    if not cfg_lic.get('license_valid', False):
        log.info("⏳ License not active — signal ignored (not trading until approved)")
        return

    symbol   = sig['symbol']
    side     = sig['side']
    entry    = sig['entry']
    tp       = sig['tp']
    sl       = sig['sl']
    # Use leverage from signal if provided, otherwise use settings
    leverage = sig.get('leverage') or get_settings()['leverage']

    state['signal_count'] += 1
    state['last_signal'] = {
        'symbol': symbol, 'side': side,
        'entry': entry, 'tp': tp, 'sl': sl,
        'time': _t(), 'results': {}
    }

    log.info(f"📡 SIGNAL: {display_name(symbol)} {'LONG' if side=='buy' else 'SHORT'} "
             f"Entry:{entry} TP:{tp} SL:{sl}")

    enabled = get_enabled_exchanges()
    if not enabled:
        log.info("No exchanges enabled — signal ignored")
        return

    s = get_settings()
    margin = s['margin_usd'] if s['margin_mode']=='fixed' else None  # percent resolved per-exchange

    for i, ex in enumerate(enabled):
        if is_coin_locked(ex, symbol):
            log.info(f"🔒 {ex}: {display_name(symbol)} already has open position — skipping")
            state['last_signal']['results'][ex] = 'locked'
            continue
        # Small delay between exchanges to avoid rate limits
        if i > 0:
            time.sleep(1)
        ok, msg = place_order_on_exchange(ex, symbol, side, tp, sl, leverage, margin)
        state['last_signal']['results'][ex] = 'ok' if ok else 'error'

# ── Telegram ───────────────────────────────────────────────
def _tg_api():
    tok = load_config().get('tg_token','')
    return f"https://api.telegram.org/bot{tok}" if tok else None

def _tg_admin():
    return int(load_config().get('tg_chat_id', 0) or 0)

def _send_tg_admin(text):
    api  = _tg_api()
    admin = _tg_admin()
    if not api or not admin: return
    try:
        requests.post(f"{api}/sendMessage",
            data={'chat_id':admin,'text':text,'parse_mode':'HTML'}, timeout=10)
    except Exception: pass

def _tg_send_menu():
    kb = {"inline_keyboard":[
        [{"text":"📊 Status","callback_data":"status"},
         {"text":"💼 Positions","callback_data":"positions"}],
        [{"text":"📜 History","callback_data":"history"},
         {"text":"📡 Last Signal","callback_data":"lastsignal"}],
        [{"text":"📍 Get IP","callback_data":"getip"},
         {"text":"🔄 Refresh","callback_data":"refresh"}],
    ]}
    text = _fmt_status()
    api  = _tg_api()
    admin = _tg_admin()
    if not api or not admin: return
    try:
        requests.post(f"{api}/sendMessage",
            data={'chat_id':admin,'text':text,'parse_mode':'HTML',
                  'reply_markup':json.dumps(kb)}, timeout=10)
    except Exception: pass

def _fmt_status():
    enabled = get_enabled_exchanges()
    exc_str = ', '.join(EXCHANGE_LABELS[e] for e in enabled) or 'None'
    ov = _overall_stats()
    w,l = ov['wins'], ov['losses']; tot=w+l
    wr = f"{w/tot*100:.0f}%" if tot else '--'
    lines = []
    if _pending_update.get('version'):
        whats_new_line = f"📝 {_pending_update['whats_new']}\n" if _pending_update.get('whats_new') else ""
        lines.append(
            f"🔔 <b>Update Available — v{_pending_update['version']}</b>\n"
            f"{whats_new_line}"
            f"Please update by running this in Termux/Terminal:\n"
            f"<code>{_update_command()}</code>\n"
            f"This message stays until you update.\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
    lines += [
        "🤖 <b>G MAX V1 — Signal Bot</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"🌐 Exchanges: {exc_str}",
        f"📡 Signals processed: {state['signal_count']}",
        f"💼 Open positions: {len(state['open_positions'])}",
        f"📈 Total PnL: <code>${state['total_pnl']:+.4f}</code>",
        f"🎯 W:{w} L:{l} WR:{wr}",
    ]
    if state['last_signal']:
        ls = state['last_signal']
        lines.append(f"\n📡 Last Signal: {display_name(ls['symbol'])} "
                     f"{'LONG' if ls['side']=='buy' else 'SHORT'} @ {ls['entry']} ({ls['time']})")
    lines.append(f"\n🕐 {_t()}")
    return '\n'.join(lines)

def _fmt_positions():
    if not state['open_positions']:
        return "💼 <b>No open positions</b>"
    lines = ["💼 <b>OPEN POSITIONS</b>","━━━━━━━━━━━━━━━━━━"]
    for key,pos in state['open_positions'].items():
        side = '🟢 LONG' if pos['side']=='buy' else '🔴 SHORT'
        lines.append(
            f"💎 <b>{display_name(pos['symbol'])}</b> [{EXCHANGE_LABELS[pos['exchange']]}] {side}\n"
            f"   Entry: <code>{pos['entry']:.6g}</code>  TP:{pos['tp']}  SL:{pos['sl']}\n"
            f"   Opened: {pos.get('opened','--')}"
        )
    return '\n'.join(lines)

def _fmt_history():
    if not state['trades']:
        return "📜 <b>No trades yet</b>"
    lines = ["📜 <b>RECENT TRADES</b>","━━━━━━━━━━━━━━━━━━"]
    for t in reversed(state['trades'][-8:]):
        icon = '🟢' if t['side']=='buy' else '🔴'
        badge = '✅ WIN' if t['pnl']>0 else ('❌ LOSS' if t['pnl']<0 else '➖')
        lines.append(
            f"{icon} <b>{display_name(t['symbol'])}</b> [{EXCHANGE_LABELS.get(t['exchange'],t['exchange'])}] "
            f"{badge} <code>${t['pnl']:+.4f}</code> {t.get('closed','')}"
        )
    return '\n'.join(lines)

def _fmt_last_signal():
    ls = state['last_signal']
    if not ls: return "📡 No signal received yet"
    results = ls.get('results',{})
    res_str = '\n'.join(
        f"  {'✅' if v=='ok' else ('🔒' if v=='locked' else '❌')} {EXCHANGE_LABELS.get(k,k)}: {v}"
        for k,v in results.items()
    )
    return (
        f"📡 <b>Last Signal</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢' if ls['side']=='buy' else '🔴'} {display_name(ls['symbol'])} "
        f"{'LONG' if ls['side']=='buy' else 'SHORT'}\n"
        f"Entry: {ls['entry']}  TP: {ls['tp']}  SL: {ls['sl']}\n"
        f"Time: {ls['time']}\n"
        f"Results:\n{res_str}"
    )

def handle_tg_update(upd):
    admin = _tg_admin()
    api   = _tg_api()

    def _edit(mid, text):
        kb = {"inline_keyboard":[
            [{"text":"📊 Status","callback_data":"status"},
             {"text":"💼 Positions","callback_data":"positions"}],
            [{"text":"📜 History","callback_data":"history"},
             {"text":"📡 Last Signal","callback_data":"lastsignal"}],
            [{"text":"📍 Get IP","callback_data":"getip"},
             {"text":"🔄 Refresh","callback_data":"refresh"}],
        ]}
        try:
            requests.post(f"{api}/editMessageText",
                data={'chat_id':admin,'message_id':mid,'text':text,'parse_mode':'HTML',
                      'reply_markup':json.dumps(kb)}, timeout=10)
        except Exception: pass

    if 'message' in upd:
        msg = upd['message']
        if msg['chat']['id'] != admin: return
        text = msg.get('text','')
        if text in ['/start','/menu','/status']:
            _tg_send_menu()
        elif text == '/positions': _send_tg_admin(_fmt_positions())
        elif text == '/history':   _send_tg_admin(_fmt_history())
        elif text == '/signal':    _send_tg_admin(_fmt_last_signal())
        elif text in ['/ip','/getip']:
            ip = _get_local_ip()
            _send_tg_admin(f"📍 Dashboard: <code>http://{ip}:5000</code>")
        else:
            _tg_send_menu()

    elif 'callback_query' in upd:
        cb  = upd['callback_query']
        if cb['from']['id'] != admin: return
        data = cb['data']
        mid  = cb['message']['message_id']
        try:
            requests.post(f"{api}/answerCallbackQuery",
                data={'callback_query_id':cb['id']}, timeout=5)
        except Exception: pass
        if data == 'status':     _edit(mid, _fmt_status())
        elif data == 'positions': _edit(mid, _fmt_positions())
        elif data == 'history':   _edit(mid, _fmt_history())
        elif data == 'lastsignal':_edit(mid, _fmt_last_signal())
        elif data == 'refresh':   _edit(mid, _fmt_status())
        elif data == 'getip':
            ip = _get_local_ip()
            _edit(mid, f"📍 <b>Dashboard</b>\n<code>http://{ip}:5000</code>")

def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8',80)); ip=s.getsockname()[0]; s.close(); return ip
    except Exception: return '127.0.0.1'

# ── Telegram polling ───────────────────────────────────────
CHANNEL_USERNAME = 'GMAXV1'   # public channel — no bot membership needed
_last_seen_msg_id = 0         # track last processed message
_channel_initialized = False  # skip history on first run

def scrape_public_channel():
    """
    Read @GMAXV1 signals via Telegram public web preview.
    On first run: records current latest ID but does NOT trade old signals.
    After that: only processes NEW signals.
    """
    global _last_seen_msg_id, _channel_initialized
    import re as _re
    try:
        r = requests.get(
            f'https://t.me/s/{CHANNEL_USERNAME}',
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=15
        )
        if r.status_code != 200:
            return

        html = r.text
        blocks = _re.findall(
            r'data-post="[^/]+/(\d+)".*?'
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            html, _re.DOTALL
        )
        if not blocks:
            return

        if not _channel_initialized:
            # First run — just record the latest message ID, skip all history
            max_id = max(int(b[0]) for b in blocks)
            _last_seen_msg_id = max_id
            _channel_initialized = True
            log.info(f"📡 Channel initialized. Latest msg ID: {max_id} — waiting for NEW signals only")
            return

        for msg_id_str, raw_html in blocks:
            msg_id = int(msg_id_str)
            if msg_id <= _last_seen_msg_id:
                continue
            _last_seen_msg_id = max(_last_seen_msg_id, msg_id)
            text = _re.sub(r'<[^>]+>', ' ', raw_html)
            text = text.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').strip()
            if not text:
                continue
            sig = parse_signal(text)
            if sig:
                log.info(f"📡 NEW signal received: {sig}")
                threading.Thread(target=process_signal, args=(sig,), daemon=True).start()
    except Exception as e:
        log.info(f"Channel scrape error: {e}")

def channel_poll_loop():
    """Poll public @GMAXV1 channel every 10 seconds. No admin needed."""
    log.info("📡 Public channel polling started (@GMAXV1)")
    while state.get('running', True):
        try:
            scrape_public_channel()
        except Exception as e:
            log.info(f"channel poll: {e}")
        time.sleep(10)

def tg_poll_loop():
    """Admin bot command loop — only handles /menu, callbacks etc."""
    api = _tg_api()
    if not api:
        log.warning("📱 TG skipped — no token")
        return
    log.info("📱 TG admin bot started")

    try:
        requests.get(f"{api}/getUpdates", params={'offset':-1,'timeout':1}, timeout=5)
    except Exception: pass

    ip = _get_local_ip()
    _send_tg_admin(
        f"🤖 <b>G MAX V1 Signal Bot Online 🟢</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📡 Reading signals from @{CHANNEL_USERNAME} (public)\n"
        f"🌐 Exchanges: {', '.join(EXCHANGE_LABELS[e] for e in get_enabled_exchanges()) or 'None'}\n"
        f"📊 Dashboard: <code>http://{ip}:5000</code>\n"
        f"Send /menu for controls"
    )

    offset = 0; fail = 0
    while state.get('running', True):
        try:
            r = requests.get(f"{api}/getUpdates",
                params={'offset':offset,'timeout':30,
                        'allowed_updates':['message','callback_query']},
                timeout=35)
            if r.status_code == 200:
                fail = 0
                for upd in r.json().get('result',[]):
                    offset = upd['update_id'] + 1
                    try: handle_tg_update(upd)
                    except Exception as e: log.info(f"TG handle: {e}")
            else:
                fail += 1; time.sleep(min(fail*5,60))
        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            log.info(f"TG poll: {e}"); fail += 1; time.sleep(min(fail*5,60))

# ── Position monitor loop ──────────────────────────────────
def monitor_loop():
    while state.get('running', True):
        try:
            _monitor_positions()
        except Exception as e:
            log.info(f"monitor loop: {e}")
        time.sleep(60)

# ── Bot start / stop ───────────────────────────────────────
_tg_thread  = None
_mon_thread = None

_chan_thread = None
_lic_thread  = None

def start_bot():
    global _tg_thread, _mon_thread, _chan_thread, _lic_thread
    load_history()
    load_open_positions()
    try:
        _reconcile_open_positions()
    except Exception as e:
        log.info(f"position reconcile error: {e}")
    state['running'] = True

    if not (_tg_thread and _tg_thread.is_alive()):
        _tg_thread = threading.Thread(target=tg_poll_loop, daemon=True)
        _tg_thread.start()

    if not (_chan_thread and _chan_thread.is_alive()):
        _chan_thread = threading.Thread(target=channel_poll_loop, daemon=True)
        _chan_thread.start()

    if not (_mon_thread and _mon_thread.is_alive()):
        _mon_thread = threading.Thread(target=monitor_loop, daemon=True)
        _mon_thread.start()

    if not (_lic_thread and _lic_thread.is_alive()):
        _lic_thread = threading.Thread(target=license_check_loop, daemon=True)
        _lic_thread.start()

def stop_bot():
    state['running'] = False

def is_running():
    return bool(_tg_thread and _tg_thread.is_alive())

# ── Stats ──────────────────────────────────────────────────
def _overall_stats():
    today = _today_str()
    gp=gl=0.0; wins=losses=0; today_pnl=0.0
    for t in state['trades']:
        if t.get('status')!='closed': continue
        pnl=t['pnl']
        if pnl>0: gp+=pnl; wins+=1
        elif pnl<0: gl+=abs(pnl); losses+=1
        if t.get('closed_date')==today: today_pnl+=pnl
    total=wins+losses
    pf = gp/gl if gl>0 else (float('inf') if gp>0 else 0.0)
    return {'total_pnl':state['total_pnl'],'today_pnl':today_pnl,
            'wins':wins,'losses':losses,'wr':wins/total*100 if total else 0,'pf':pf}

def _symbol_stats():
    stats = defaultdict(lambda:{'wins':0,'losses':0,'total_pnl':0.0,'gp':0.0,'gl':0.0,'symbol':None,'exchange':None})
    for t in state['trades']:
        if t.get('status')!='closed': continue
        key = display_name(t['symbol'])
        d = stats[key]
        d['total_pnl'] += t['pnl']
        d['symbol'] = t['symbol']
        d['exchange'] = t.get('exchange','')
        if t['pnl']>0: d['wins']+=1; d['gp']+=t['pnl']
        elif t['pnl']<0: d['losses']+=1; d['gl']+=abs(t['pnl'])
    out={}
    for key,d in stats.items():
        total=d['wins']+d['losses']
        pf = d['gp']/d['gl'] if d['gl']>0 else (float('inf') if d['gp']>0 else 0.0)
        out[key]={**d,'wr':d['wins']/total*100 if total else 0,'total':total,'pf':pf}
    return out

def _coin_stats_ranked():
    ss = _symbol_stats()
    return sorted(ss.items(), key=lambda x:x[1]['wr'], reverse=True)

def _pf_display(pf):
    if pf == float('inf'): return '∞'
    if pf == 0.0: return '--'
    return f"{pf:.2f}"

def _advanced_stats():
    closed = [t for t in state['trades'] if t.get('status')=='closed']
    if not closed:
        return {'equity_curve':[],'max_drawdown':0.0,'max_drawdown_pct':0.0,
                'cur_streak':0,'cur_streak_type':'--','max_win_streak':0,'max_loss_streak':0,
                'avg_win':0.0,'avg_loss':0.0,'avg_rr':0.0,'best_trade':None,'worst_trade':None,
                'total_trades':0}
    equity=[]; running=0.0; peak=0.0; max_dd=0.0; max_dd_pct=0.0
    for t in closed:
        running+=t['pnl']; peak=max(peak,running)
        dd=peak-running
        if dd>max_dd:
            max_dd=dd; max_dd_pct=(dd/peak*100) if peak>0 else 0.0
        equity.append(round(running,4))
    wins=[t['pnl'] for t in closed if t['pnl']>0]
    losses=[t['pnl'] for t in closed if t['pnl']<0]
    avg_win=sum(wins)/len(wins) if wins else 0.0
    avg_loss=sum(losses)/len(losses) if losses else 0.0
    avg_rr=abs(avg_win/avg_loss) if avg_loss else 0.0
    cur_streak=0; cur_type='--'
    for t in reversed(closed):
        kind='win' if t['pnl']>0 else ('loss' if t['pnl']<0 else None)
        if kind is None: break
        if cur_type=='--': cur_type=kind
        if kind!=cur_type: break
        cur_streak+=1
    max_w=cur_w=0; max_l=cur_l=0
    for t in closed:
        if t['pnl']>0: cur_w+=1; cur_l=0; max_w=max(max_w,cur_w)
        elif t['pnl']<0: cur_l+=1; cur_w=0; max_l=max(max_l,cur_l)
        else: cur_w=cur_l=0
    best=max(closed,key=lambda t:t['pnl'])
    worst=min(closed,key=lambda t:t['pnl'])
    return {'equity_curve':equity,'max_drawdown':round(max_dd,4),'max_drawdown_pct':round(max_dd_pct,2),
            'cur_streak':cur_streak,'cur_streak_type':cur_type,
            'max_win_streak':max_w,'max_loss_streak':max_l,
            'avg_win':round(avg_win,4),'avg_loss':round(avg_loss,4),'avg_rr':round(avg_rr,2),
            'best_trade':{'symbol':display_name(best['symbol']),'pnl':best['pnl']},
            'worst_trade':{'symbol':display_name(worst['symbol']),'pnl':worst['pnl']},
            'total_trades':len(closed)}

def _daily_pnl_series(days=14):
    from datetime import date
    totals = defaultdict(float)
    for t in state['trades']:
        if t.get('status')!='closed': continue
        d=t.get('closed_date')
        if d: totals[d]+=t['pnl']
    today=date.today(); out=[]
    for i in range(days-1,-1,-1):
        d=(today-timedelta(days=i)).strftime('%Y-%m-%d')
        label=(today-timedelta(days=i)).strftime('%m/%d')
        out.append({'date':d,'label':label,'pnl':round(totals.get(d,0.0),4)})
    return out


# ══════════════════════════════════════════════════════════
#  THEME CSS
# ══════════════════════════════════════════════════════════
THEME_CSS = '''
:root{
  --bg:#070d1a; --card:#0d1526; --border:#1a2840;
  --green:#00e87a; --red:#ff4060; --yellow:#ffd700; --blue:#4499ff;
  --text:#ffffff; --textdim:#aabbdd; --textdim2:#7788aa; --faint:#445566;
  --orange:#ff8800; --tg:#229ED9;
  --green-rgb:0,232,122; --red-rgb:255,64,96; --yellow-rgb:255,215,0; --blue-rgb:68,153,255;
  --orange-rgb:255,136,0; --tg-rgb:34,158,217; --border-rgb:26,40,64; --card-rgb:13,21,38;
  --font:'Courier New',monospace;
}
body[data-theme="cyber"]{
  --bg:#05010f; --card:#12082a; --border:#2d1b54;
  --green:#00ffcc; --red:#ff2079; --yellow:#ffe93a; --blue:#9d7bff;
  --text:#f2ecff; --textdim:#b9a8e0; --textdim2:#8874b8; --faint:#4a3a72;
  --orange:#ff9d00; --tg:#229ED9;
  --green-rgb:0,255,204; --red-rgb:255,32,121; --yellow-rgb:255,233,58; --blue-rgb:157,123,255;
  --orange-rgb:255,157,0; --border-rgb:45,27,84; --card-rgb:18,8,42;
}
body[data-theme="aurora"]{
  --bg:#071019; --card:#0e1f2b; --border:#1c3b4d;
  --green:#2dd4bf; --red:#f43f5e; --yellow:#fbbf24; --blue:#60a5fa;
  --text:#eaf6f6; --textdim:#9fc9c9; --textdim2:#6b98a0; --faint:#33525c;
  --orange:#fb923c;
  --green-rgb:45,212,191; --red-rgb:244,63,94; --yellow-rgb:251,191,36; --blue-rgb:96,165,250;
  --orange-rgb:251,146,60; --border-rgb:28,59,77; --card-rgb:14,31,43;
}
body[data-theme="solar"]{
  --bg:#f5f7fb; --card:#ffffff; --border:#dde3ec;
  --green:#059669; --red:#dc2626; --yellow:#b45309; --blue:#2563eb;
  --text:#10151f; --textdim:#334155; --textdim2:#586173; --faint:#7c8695;
  --orange:#c2410c;
  --green-rgb:5,150,105; --red-rgb:220,38,38; --yellow-rgb:180,83,9; --blue-rgb:37,99,235;
  --orange-rgb:194,65,12; --border-rgb:221,227,236; --card-rgb:255,255,255;
}
body[data-theme="matrix"]{
  --bg:#000000; --card:#050f05; --border:#113311;
  --green:#00ff41; --red:#ff3333; --yellow:#ccff00; --blue:#33ffcc;
  --text:#d4ffd4; --textdim:#66cc66; --textdim2:#449944; --faint:#225522;
  --orange:#ffaa00;
  --green-rgb:0,255,65; --red-rgb:255,51,51; --yellow-rgb:204,255,0; --blue-rgb:51,255,204;
  --orange-rgb:255,170,0; --border-rgb:17,51,17; --card-rgb:5,15,5;
}
body[data-theme="sunset"]{
  --bg:#1a0b2e; --card:#2a1245; --border:#4a2570;
  --green:#22d3a5; --red:#ff5470; --yellow:#ffb347; --blue:#7dd3fc;
  --text:#fff0f5; --textdim:#d8a8c8; --textdim2:#a878a0; --faint:#5a3a58;
  --orange:#ff7849;
  --green-rgb:34,211,165; --red-rgb:255,84,112; --yellow-rgb:255,179,71; --blue-rgb:125,211,252;
  --orange-rgb:255,120,73; --border-rgb:74,37,112; --card-rgb:42,18,69;
}
body[data-theme="arceus_white"]{
  --bg:#f0f4ff; --card:#ffffff; --border:#c8d4f0;
  --green:#00a550; --red:#e8001c; --yellow:#b35c00; --blue:#1a56db;
  --text:#0a0f1e; --textdim:#1e2d5a; --textdim2:#3a4d80; --faint:#6b7db3;
  --orange:#c44800;
  --green-rgb:0,165,80; --red-rgb:232,0,28; --yellow-rgb:179,92,0; --blue-rgb:26,86,219;
  --orange-rgb:196,72,0; --border-rgb:200,212,240; --card-rgb:255,255,255;
}
body[data-theme="pikachu_strike"]{
  --bg:#fffef5; --card:#ffffff; --border:#e8d800;
  --green:#1a8a00; --red:#cc0000; --yellow:#d4a000; --blue:#1a1a1a;
  --text:#111111; --textdim:#2a2a00; --textdim2:#5a5200; --faint:#b8a800;
  --orange:#c45c00;
  --green-rgb:26,138,0; --red-rgb:204,0,0; --yellow-rgb:212,160,0; --blue-rgb:26,26,26;
  --orange-rgb:196,92,0; --border-rgb:232,216,0; --card-rgb:255,255,255;
}
body[data-theme="pikachu_strike"] .card{border:2px solid #1a1a1a;box-shadow:3px 3px 0px #e8d800}
body[data-theme="pikachu_strike"] .section{border:1.5px solid #1a1a1a;box-shadow:2px 2px 0px #e8d800}
body[data-theme="pikachu_strike"] .btn{background:#1a1a1a;color:#e8d800;border-color:#1a1a1a}

/* ── LUCARIO — Blue/Steel/Dark ── */
body[data-theme="lucario"]{
  --bg:#0b1120; --card:#131d30; --border:#2a3d66;
  --green:#4de8c2; --red:#ff5566; --yellow:#f0c040; --blue:#7eb8ff;
  --text:#e8f0ff; --textdim:#8ba8d8; --textdim2:#5570a0; --faint:#2a3d66;
  --orange:#ffaa44;
  --green-rgb:77,232,194; --red-rgb:255,85,102; --yellow-rgb:240,192,64; --blue-rgb:126,184,255;
  --orange-rgb:255,170,68; --border-rgb:42,61,102; --card-rgb:19,29,48;
}
body[data-theme="lucario"] h1{
  background: linear-gradient(90deg,#7eb8ff,#4de8c2);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  text-shadow:none;
}
body[data-theme="lucario"] .card{
  border:1px solid rgba(126,184,255,0.3);
  box-shadow:0 0 12px rgba(126,184,255,0.08),inset 0 0 20px rgba(126,184,255,0.03);
}
body[data-theme="lucario"] .section{
  border:1px solid rgba(126,184,255,0.25);
  box-shadow:0 0 8px rgba(126,184,255,0.06);
}
body[data-theme="lucario"] .sh{color:#7eb8ff}
body[data-theme="lucario"] .val.b{color:#7eb8ff;text-shadow:0 0 8px rgba(126,184,255,0.5)}
body[data-theme="lucario"] .val.g{color:#4de8c2;text-shadow:0 0 8px rgba(77,232,194,0.4)}
body[data-theme="lucario"] .badge.bg{background:rgba(77,232,194,0.12);color:#4de8c2;border-color:rgba(77,232,194,0.3)}
body[data-theme="lucario"] .badge.bs{background:rgba(255,85,102,0.12);color:#ff5566;border-color:rgba(255,85,102,0.3)}
body[data-theme="lucario"] .btn{background:rgba(126,184,255,0.1);border-color:rgba(126,184,255,0.35);color:#7eb8ff}
body[data-theme="lucario"]::before{
  content:''; display:block; height:3px; position:fixed; top:0; left:0; right:0; z-index:999;
  background:linear-gradient(90deg,#7eb8ff 0%,#4de8c2 50%,#7eb8ff 100%);
}

/* ── CHARIZARD — Fire/Orange ── */
body[data-theme="charizard"]{
  --bg:#1a0800; --card:#2d1200; --border:#6b2e00;
  --green:#22d3a5; --red:#ff3300; --yellow:#ffcc00; --blue:#66aaff;
  --text:#fff8f0; --textdim:#e8a878; --textdim2:#a06840; --faint:#6b2e00;
  --orange:#ff7700;
  --green-rgb:34,211,165; --red-rgb:255,51,0; --yellow-rgb:255,204,0; --blue-rgb:102,170,255;
  --orange-rgb:255,119,0; --border-rgb:107,46,0; --card-rgb:45,18,0;
}
body[data-theme="charizard"] h1{
  background:linear-gradient(90deg,#ff7700,#ffcc00,#ff3300);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  text-shadow:none;
}
body[data-theme="charizard"] .card{
  border:1px solid rgba(255,119,0,0.35);
  box-shadow:0 0 14px rgba(255,119,0,0.1),inset 0 0 20px rgba(255,80,0,0.04);
}
body[data-theme="charizard"] .section{
  border:1px solid rgba(255,119,0,0.25);
  box-shadow:0 0 8px rgba(255,80,0,0.08);
}
body[data-theme="charizard"] .sh{color:#ff9933;letter-spacing:1.5px}
body[data-theme="charizard"] .val.b{color:#ffcc00;text-shadow:0 0 8px rgba(255,204,0,0.5)}
body[data-theme="charizard"] .val.g{color:#22d3a5}
body[data-theme="charizard"] .badge.bg{background:rgba(34,211,165,0.12);color:#22d3a5;border-color:rgba(34,211,165,0.3)}
body[data-theme="charizard"] .badge.bs{background:rgba(255,51,0,0.12);color:#ff5533;border-color:rgba(255,51,0,0.3)}
body[data-theme="charizard"] .btn{background:rgba(255,119,0,0.12);border-color:rgba(255,119,0,0.4);color:#ff9933}
body[data-theme="charizard"] .linkbar a{color:#ffcc00}
body[data-theme="charizard"]::before{
  content:''; display:block; height:4px; position:fixed; top:0; left:0; right:0; z-index:999;
  background:linear-gradient(90deg,#ff3300 0%,#ff7700 40%,#ffcc00 70%,#ff3300 100%);
}
'''

# ══════════════════════════════════════════════════════════
#  HTML TEMPLATES
# ══════════════════════════════════════════════════════════

SETUP_HTML = '''<!DOCTYPE html><html><head>
<title>G Max V1 — Setup</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;
  padding:20px;min-height:100vh;display:flex;align-items:center;justify-content:center}
.box{background:var(--card);border:1px solid var(--border);border-radius:12px;
  padding:24px;max-width:480px;width:100%}
h1{color:var(--green);font-size:18px;letter-spacing:2px;text-align:center;margin-bottom:2px;font-weight:bold}
.sub{text-align:center;color:var(--textdim);font-size:10px;margin-bottom:16px;font-weight:bold}
.sec{font-size:10px;color:var(--green);text-transform:uppercase;letter-spacing:2px;
  font-weight:bold;margin:14px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--border)}
label{display:block;font-size:10px;color:var(--textdim);text-transform:uppercase;
  letter-spacing:1px;margin-bottom:5px;font-weight:bold}
input[type=text],input[type=password]{width:100%;background:var(--bg);border:1px solid var(--border);
  border-radius:6px;padding:10px;color:var(--text);font-family:'Courier New',monospace;
  font-size:11px;margin-bottom:10px;outline:none}
input:focus{border-color:rgba(var(--green-rgb),0.4)}
button{width:100%;background:rgba(var(--green-rgb),0.094);border:1px solid rgba(var(--green-rgb),0.333);
  color:var(--green);padding:13px;border-radius:6px;font-family:'Courier New',monospace;
  font-size:13px;cursor:pointer;font-weight:bold;margin-top:6px}
.warn{background:rgba(var(--yellow-rgb),0.067);border:1px solid rgba(var(--yellow-rgb),0.267);
  border-radius:6px;padding:9px;font-size:11px;color:var(--yellow);margin-bottom:10px;line-height:1.8;font-weight:bold}
.safe{background:rgba(var(--green-rgb),0.067);border:1px solid rgba(var(--green-rgb),0.2);
  border-radius:8px;padding:11px;font-size:11px;color:var(--green);margin-bottom:14px;line-height:2}
.err{background:rgba(var(--red-rgb),0.067);border:1px solid rgba(var(--red-rgb),0.267);
  border-radius:6px;padding:10px;font-size:12px;color:var(--red);margin-bottom:14px;font-weight:bold}
.opt{color:var(--faint);font-size:9px;text-transform:none;font-weight:normal}
.req{color:var(--red);font-size:9px}
.tgi input{border-color:rgba(var(--tg-rgb,34,158,217),0.2)}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
</style></head><body><div class="box">
<h1>🤖 G MAX V1</h1>
<div class="sub">Signal Bot — Engineered by Paqu</div>

<div class="safe">
🔒 <b>Your API Keys Are Safe</b><br>
✅ All keys stored locally on YOUR device only<br>
✅ We have NO access to your API keys or funds<br>
✅ Enable <b>Futures trading only</b> — never Withdrawals<br>
✅ Use IP-restricted API keys for extra security
</div>

{% if error %}<div class="err">❌ {{error}}</div>{% endif %}

<form method="POST" action="/setup">

<div class="sec">📊 Exchange APIs <span class="req">* at least one required</span></div>
<div class="warn">⚠️ Enable Futures only · Never enable Withdrawals · Restrict to your IP</div>

{% for ex, label in exchanges %}
<div class="sec" style="color:var(--blue)">{{label}}</div>
<label>{{label}} API Key <span class="opt">(leave blank to skip)</span></label>
<input type="text" name="{{ex}}_key" placeholder="{{label}} API key" autocomplete="off" spellcheck="false">
<label>{{label}} Secret Key</label>
<input type="password" name="{{ex}}_secret" placeholder="{{label}} Secret key">
{% if ex in ('kucoin','okx','bitget') %}
<label>Passphrase <span class="opt">(required for {{label}})</span></label>
<input type="text" name="{{ex}}_passphrase" placeholder="Trading passphrase">
{% endif %}
{% endfor %}

<div class="sec">📱 Telegram <span class="req">* required</span></div>
<label>Bot Token</label>
<input type="text" name="tg_token" placeholder="1234567890:ABCdef..." class="tgi">
<label>Your Admin User ID</label>
<input type="text" name="tg_chat_id" placeholder="e.g. 6783713687" class="tgi">

<div class="sec">🔑 License</div>
<div class="safe">
✅ Your bot token IS your license<br>
✅ Enter your bot token above — Paqu Trading will activate it<br>
✅ Contact support if not activated yet
</div>
<button type="submit">✅ SAVE &amp; START BOT</button>
</form>

<div style="text-align:center;color:var(--faint);font-size:9px;margin-top:16px;line-height:2">
Engineered by Paqu · Server Hosted by Paqu<br>
Strategy by Paqu · 2 Years Backtested by Paqu
</div>
</div></body></html>'''


DASH_HTML = '''<!DOCTYPE html><html><head>
<title>G Max V1</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;padding:10px;font-size:12px}
h1{color:var(--green);font-size:15px;letter-spacing:2px;text-align:center;padding:8px 0 2px;font-weight:bold}
.sub{text-align:center;color:var(--textdim);font-size:10px;margin-bottom:5px;font-weight:bold}
.refresh{text-align:center;background:rgba(var(--green-rgb),0.06);border:1px solid rgba(var(--green-rgb),0.2);
  border-radius:6px;padding:5px;font-size:11px;color:var(--green);font-weight:bold;margin-bottom:6px}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px;margin-bottom:7px}
.card{background:var(--card);border:1px solid var(--border);border-radius:7px;padding:8px 6px;text-align:center}
.lbl{font-size:9px;color:var(--textdim);text-transform:uppercase;letter-spacing:1px;font-weight:bold}
.val{font-size:17px;font-weight:bold;margin-top:3px}
.g{color:var(--green)}.r{color:var(--red)}.y{color:var(--yellow)}.b{color:var(--blue)}
.section{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:9px;margin-bottom:7px}
.sh{font-size:10px;color:var(--green);text-transform:uppercase;letter-spacing:1.5px;font-weight:bold;
  margin-bottom:7px;padding-bottom:5px;border-bottom:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:center}
.pos-row,.trade-row{background:var(--bg);border:1px solid var(--border);border-radius:6px;
  padding:7px 8px;margin-bottom:5px;font-size:11px}
.row-top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:4px}
.coin{font-weight:bold}
.coin a{color:var(--text);text-decoration:none;border-bottom:1px dotted var(--blue)}
.side-l{color:var(--green)}.side-s{color:var(--red)}
.small{font-size:9.5px;color:var(--textdim2);margin-top:3px}
.empty{text-align:center;color:var(--faint);padding:14px;font-size:11px}
.linkbar{display:flex;gap:6px;margin-bottom:7px}
.linkbar a{flex:1;text-align:center;background:var(--card);border:1px solid var(--border);
  border-radius:6px;padding:8px;color:var(--blue);text-decoration:none;font-size:10px;font-weight:bold}
.btn{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:8px 4px;
  font-family:'Courier New',monospace;font-size:10px;font-weight:bold;cursor:pointer;
  text-align:center;color:var(--text);width:100%}
.tag{background:var(--border);border-radius:4px;padding:1px 5px;font-size:8.5px;color:var(--blue);margin-left:4px}
.exch-tag{padding:1px 6px;border-radius:3px;font-size:8.5px;font-weight:bold;margin-left:4px;
  background:rgba(var(--blue-rgb),0.15);color:var(--blue);border:1px solid rgba(var(--blue-rgb),0.3)}
.badge{padding:2px 6px;border-radius:3px;font-size:9px;font-weight:bold}
.bg{background:rgba(var(--green-rgb),0.133);color:var(--green);border:1px solid rgba(var(--green-rgb),0.333)}
.bs{background:rgba(var(--red-rgb),0.133);color:var(--red);border:1px solid rgba(var(--red-rgb),0.333)}
.bw{background:var(--border);color:var(--textdim);border:1px solid var(--border)}
.b-close{background:rgba(var(--red-rgb),0.094);border:1px solid rgba(var(--red-rgb),0.333);
  color:var(--red);border-radius:4px;padding:3px 8px;font-size:9px;font-weight:bold;cursor:pointer}

/* Exchange status bar */
.exc-bar{background:var(--card);border:1px solid var(--border);border-radius:8px;
  padding:8px 10px;margin-bottom:7px}
.exc-bar-title{font-size:9px;color:var(--textdim2);font-weight:bold;text-transform:uppercase;
  letter-spacing:1px;margin-bottom:6px}
.exc-row{display:flex;gap:6px;flex-wrap:wrap}
.exc-chip{display:flex;align-items:center;gap:4px;padding:4px 8px;border-radius:5px;
  font-size:10px;font-weight:bold;border:1px solid var(--border)}
.exc-chip.ok{border-color:rgba(var(--green-rgb),0.3);background:rgba(var(--green-rgb),0.07);color:var(--green)}
.exc-chip.err{border-color:rgba(var(--red-rgb),0.3);background:rgba(var(--red-rgb),0.07);color:var(--red)}
.exc-chip.off{border-color:var(--border);color:var(--faint)}
.exc-dot{width:6px;height:6px;border-radius:50%}
.exc-dot.ok{background:var(--green);box-shadow:0 0 5px rgba(var(--green-rgb),0.7)}
.exc-dot.err{background:var(--red)}
.exc-dot.off{background:var(--faint)}

/* Signal status box */
.signal-box{background:var(--card);border:1px solid var(--border);border-radius:8px;
  padding:8px 10px;margin-bottom:7px}
.sig-header{font-size:10px;font-weight:bold;letter-spacing:1px;text-transform:uppercase;
  color:var(--textdim2);margin-bottom:5px}
.sig-main{font-size:12px;font-weight:bold}
.sig-meta{font-size:9.5px;color:var(--textdim2);margin-top:3px}
.sig-results{display:flex;flex-wrap:wrap;gap:5px;margin-top:5px}
.sig-r{padding:2px 7px;border-radius:4px;font-size:9px;font-weight:bold}
.sig-r.ok{background:rgba(var(--green-rgb),0.1);color:var(--green);border:1px solid rgba(var(--green-rgb),0.3)}
.sig-r.err{background:rgba(var(--red-rgb),0.1);color:var(--red);border:1px solid rgba(var(--red-rgb),0.3)}
.sig-r.locked{background:rgba(var(--yellow-rgb),0.1);color:var(--yellow);border:1px solid rgba(var(--yellow-rgb),0.3)}

/* Warning banners */
.warn-box{background:rgba(var(--yellow-rgb),0.06);border:1px solid rgba(var(--yellow-rgb),0.3);
  border-radius:6px;padding:7px 10px;font-size:10px;color:var(--yellow);margin-bottom:6px;font-weight:bold}
</style>
</head><body>
<h1>🤖 G MAX V1</h1>
<div class="sub">Signal Bot · Engineered by Paqu</div>
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
  <div class="refresh" style="flex:1;margin:0">🔄 Auto-refresh 30s | {{now}}</div>
  <a href="/contact" style="position:relative;text-decoration:none;margin-left:8px;
     background:var(--card);border:1px solid var(--border);border-radius:8px;
     padding:6px 10px;font-size:13px">
    💬 Contact Us
  </a>
</div>

<!-- Exchange Status Bar -->
<div class="exc-bar">
  <div class="exc-bar-title">🌐 Connected Exchanges</div>
  <div class="exc-row">
  {% for ex, label, status in exchange_chips %}
  <div class="exc-chip {{status}}">
    <div class="exc-dot {{status}}"></div>
    {{label}}
  </div>
  {% endfor %}
  {% if not exchange_chips %}
  <span style="color:var(--red);font-size:10px;font-weight:bold">⚠️ No exchanges configured</span>
  {% endif %}
  </div>
</div>

<!-- Latest Signal -->
{% if last_signal %}
<div class="signal-box">
  <div class="sig-header">📡 Latest Signal</div>
  <div class="sig-main">
    {{'🟢' if last_signal.side=='buy' else '🔴'}}
    <span class="{{'g' if last_signal.side=='buy' else 'r'}}">
      {{'Long/Buy' if last_signal.side=='buy' else 'Short/Sell'}}
    </span>
    <b>{{display_name(last_signal.symbol)}}/USDT</b>
  </div>
  <div class="sig-meta">
    Entry: {{last_signal.entry}} · TP: {{last_signal.tp}} · SL: {{last_signal.sl}} · {{last_signal.time}}
  </div>
  {% if last_signal.results %}
  <div class="sig-results">
  {% for ex, res in last_signal.results.items() %}
    <span class="sig-r {{res}}">{{exchange_labels[ex]}}: {{'✅ Opened' if res=='ok' else ('🔒 Locked' if res=='locked' else '❌ Failed')}}</span>
  {% endfor %}
  </div>
  {% endif %}
</div>
{% endif %}

{% if token_status == 'pending' %}
<div style="background:rgba(255,215,0,0.08);border:1px solid rgba(255,215,0,0.4);
  border-radius:8px;padding:9px 12px;margin-bottom:7px;font-size:11px;color:#ffd700;font-weight:bold">
  ⏳ <b>Awaiting Approval</b> — Bot is running and receiving signals.
  Trades will open once Paqu Trading approves your bot token.
  <span style="font-size:9px;opacity:0.7">(checking every 30s)</span>
</div>
{% elif token_status == 'active' %}
<div style="background:rgba(0,232,122,0.08);border:1px solid rgba(0,232,122,0.3);
  border-radius:8px;padding:7px 12px;margin-bottom:7px;font-size:11px;color:#00e87a;font-weight:bold">
  ✅ <b>Bot Token Connected</b> — Signals active · Expires: {{license_expiry}}
</div>
{% endif %}

{% if update_available %}
<div style="background:rgba(255,140,0,0.1);border:1px solid rgba(255,140,0,0.45);
  border-radius:8px;padding:9px 12px;margin-bottom:7px;font-size:11px;color:#ff8c00;font-weight:bold">
  🔔 <b>Update Available — v{{latest_version}}</b><br>
  {% if whats_new %}
  <span style="font-weight:normal">📝 {{whats_new}}</span><br>
  {% endif %}
  <span style="font-weight:normal">Please update to the latest version. Run this in Termux/Terminal:</span>
  <div style="background:rgba(0,0,0,0.25);border-radius:6px;padding:6px 8px;margin-top:5px;
    font-family:monospace;font-size:10px;word-break:break-all;font-weight:normal">
    {{update_command}}
  </div>
  <span style="font-size:9px;opacity:0.7;font-weight:normal">This message stays until you update — everything else keeps working normally.</span>
</div>
{% endif %}

{% for w in warnings %}
<div class="warn-box">⚠️ {{w}}</div>
{% endfor %}

<!-- Balance Card -->
<div class="section" style="margin-bottom:7px">
<div class="sh"><span>💰 Account Balance</span><span class="g">${{'{:.2f}'.format(total_balance)}}</span></div>
{% if balance_items %}
{% for label, bal in balance_items %}
<div class="small" style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--border)">
  <span>{{label}}</span><span class="g" style="font-weight:bold">${{'%.2f'|format(bal)}} USDT</span>
</div>
{% endfor %}
{% else %}
<div class="empty">Fetching balance...</div>
{% endif %}
</div>

<!-- Stats Grid -->
<div class="grid">
<div class="card"><div class="lbl">Today PnL</div>
  <div class="val {{'g' if today_pnl>=0 else 'r'}}">${{'%+.3f'|format(today_pnl)}}</div></div>
<div class="card"><div class="lbl">Total PnL</div>
  <div class="val {{'g' if total_pnl>=0 else 'r'}}">${{'%+.3f'|format(total_pnl)}}</div></div>
<div class="card"><div class="lbl">Win Rate</div><div class="val y">{{wr}}</div></div>
<div class="card"><div class="lbl">Profit Factor</div><div class="val y">{{pf_display}}</div></div>
<div class="card"><div class="lbl">Open Trades</div><div class="val b">{{pos_count}}</div></div>
<div class="card"><div class="lbl">Total Trades</div><div class="val b">{{trade_count}}</div></div>
</div>

<a href="/data" style="display:block;text-align:center;background:var(--card);border:1px solid var(--border);
border-radius:8px;padding:10px;color:var(--blue);text-decoration:none;font-size:11px;font-weight:900;
margin-bottom:7px;letter-spacing:0.5px">📊 DATA CENTER — P&L chart, drawdown, top performers ›</a>

<div class="linkbar">
  <a href="/history">📜 Full History</a>
  <a href="/settings">⚙️ Settings</a>
</div>

<!-- Open Positions -->
<div class="section">
<div class="sh"><span>💼 Open Positions</span><span>{{pos_count}}</span></div>
{% if positions %}
{% for p in positions %}
<div class="pos-row">
  <div class="row-top">
    <span class="coin"><a href="/coin/{{p.symbol}}">{{p.name}}</a>
      <span class="exch-tag">{{p.exchange_label}}</span></span>
    <span class="{{'side-l' if p.side=='buy' else 'side-s'}}">
      {{'🟢 LONG' if p.side=='buy' else '🔴 SHORT'}}</span>
    <form method="POST" action="/close/{{p.exchange}}/{{p.symbol}}" style="display:inline">
      <button type="submit" class="b-close" onclick="return confirm('Close {{p.name}} on {{p.exchange_label}}?')">✕ Close</button>
    </form>
  </div>
  <div class="small">Entry ${{p.entry}} · TP {{p.tp}} · SL {{p.sl}} · Lev {{p.leverage}}x · {{p.opened}}</div>
</div>
{% endfor %}
{% else %}<div class="empty">No open positions — waiting for signals</div>{% endif %}
</div>

<!-- Top Coins -->
<div class="section">
<div class="sh"><span>🏆 Top Coins</span></div>
{% if top_coins %}
{% for tc in top_coins %}
<div class="small">{{loop.index}}.
  <a href="/coin/{{tc.symbol}}" style="color:var(--text);font-weight:900;text-decoration:none;border-bottom:1px dotted var(--blue)">{{tc.name}}</a>
  — {{'%.0f'|format(tc.wr)}}% WR ({{tc.wins}}W/{{tc.losses}}L) <span class="{{'g' if tc.pnl>=0 else 'r'}}">${{'%+.3f'|format(tc.pnl)}}</span>
</div>
{% endfor %}
{% else %}<div class="empty">No completed trades yet</div>{% endif %}
</div>

<!-- Recent Trades (20 shown) -->
<div class="section">
<div class="sh"><span>📜 Recent Trades</span><span>{{trade_count}} total</span></div>
{% if recent_trades %}
{% for t in recent_trades %}
<div class="trade-row">
  <div class="row-top">
    <span class="coin"><a href="/coin/{{t.symbol}}">{{t.name}}</a>
      <span class="exch-tag">{{t.exchange_label}}</span></span>
    <span class="{{'g' if t.pnl>=0 else 'r'}}">{{'✅' if t.pnl>0 else ('❌' if t.pnl<0 else '➖')}} ${{'%+.3f'|format(t.pnl)}}</span>
  </div>
  <div class="small">{{'LONG' if t.side=='buy' else 'SHORT'}} · {{t.closed}} · {{t.reason}}</div>
</div>
{% endfor %}
<a href="/history" style="display:block;text-align:center;margin-top:8px;color:var(--blue);
  font-size:11px;text-decoration:none;padding:6px;border:1px solid var(--border);border-radius:6px">
  📜 View All {{trade_count}} Trades →</a>
{% else %}<div class="empty">No trades yet — waiting for first signal</div>{% endif %}
</div>

<div style="text-align:center;color:var(--faint);font-size:9px;padding:10px 0 4px;line-height:2">
  Started {{start_time}}<br>
  Engineered by Paqu · Server Hosted by Paqu<br>
  Strategy by Paqu · 2 Years Backtested by Paqu
</div>
</body></html>'''


SETTINGS_HTML = '''<!DOCTYPE html><html><head>
<title>Settings — G Max V1</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;padding:16px;font-size:12px}
.box{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:18px;max-width:480px;margin:0 auto}
h1{color:var(--green);font-size:15px;text-align:center;margin-bottom:14px;letter-spacing:1.5px}
label{display:block;font-size:10px;color:var(--textdim);text-transform:uppercase;letter-spacing:1px;margin-bottom:5px;font-weight:bold}
input[type=text],input[type=password],input[type=number]{width:100%;background:var(--bg);border:1px solid var(--border);
  border-radius:6px;padding:10px;color:var(--text);font-family:'Courier New',monospace;font-size:12px;margin-bottom:12px}
button[type=submit]{width:100%;background:rgba(var(--green-rgb),0.094);border:1px solid rgba(var(--green-rgb),0.333);
  color:var(--green);padding:12px;border-radius:6px;font-family:'Courier New',monospace;
  font-size:13px;font-weight:bold;cursor:pointer}
a.back{display:block;text-align:center;color:var(--blue);margin-top:12px;font-size:11px;text-decoration:none}
.sec{font-size:10px;color:var(--green);text-transform:uppercase;letter-spacing:2px;
  font-weight:bold;margin:14px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--border)}
.ok{background:rgba(var(--green-rgb),0.067);border:1px solid rgba(var(--green-rgb),0.267);color:var(--green);
  border-radius:6px;padding:8px;text-align:center;margin-bottom:14px;font-weight:bold;font-size:11px}
.opt{color:var(--faint);font-size:9px;text-transform:none;font-weight:normal}
.theme-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:14px}
.theme-opt{position:relative}
.theme-opt input{position:absolute;opacity:0;width:100%;height:100%;margin:0;cursor:pointer}
.theme-opt label{background:var(--bg);border:1px solid var(--border);border-radius:6px;
  padding:9px 6px;text-align:center;font-size:10px;font-weight:bold;margin:0;cursor:pointer;
  text-transform:none;letter-spacing:0}
.theme-opt input:checked + label{border-color:var(--green);color:var(--green);background:rgba(var(--green-rgb),0.094)}
.radio-row{display:flex;gap:6px;margin-bottom:12px}
.radio-opt{flex:1;position:relative}
.radio-opt input{position:absolute;opacity:0;width:100%;height:100%;margin:0;cursor:pointer}
.radio-opt label{display:block;background:var(--bg);border:1px solid var(--border);border-radius:6px;
  padding:9px 6px;text-align:center;font-size:10px;font-weight:bold;margin:0;cursor:pointer;text-transform:none;letter-spacing:0}
.radio-opt input:checked + label{border-color:var(--blue);color:var(--blue);background:rgba(var(--blue-rgb),0.094)}
.locked-box{background:rgba(var(--orange-rgb),0.06);border:1px solid rgba(var(--orange-rgb),0.3);
  border-radius:8px;padding:10px;margin-bottom:14px}
.locked-label{font-size:10px;color:var(--orange);font-weight:bold;margin-bottom:5px;text-transform:uppercase;letter-spacing:1px}
.locked-val{font-size:18px;font-weight:bold;color:var(--yellow)}
.locked-note{font-size:9px;color:var(--orange);margin-top:4px;line-height:1.6}
.warn2{background:rgba(var(--orange-rgb),0.094);border:1px solid rgba(var(--orange-rgb),0.333);
  border-radius:6px;padding:9px;font-size:10px;color:var(--orange);margin-bottom:12px;line-height:1.6}
.exc-toggle{display:flex;align-items:center;justify-content:space-between;padding:8px 10px;
  background:var(--bg);border:1px solid var(--border);border-radius:6px;margin-bottom:6px}
.exc-name{font-size:11px;font-weight:bold;color:var(--text)}
.exc-status{font-size:9.5px;color:var(--textdim2)}
.toggle-row{display:flex;gap:5px}
.t-btn{padding:4px 10px;border-radius:4px;font-size:9px;font-weight:bold;cursor:pointer;
  font-family:'Courier New',monospace;border:1px solid}
.t-on{background:rgba(var(--green-rgb),0.1);border-color:var(--green);color:var(--green)}
.t-off{background:rgba(var(--red-rgb),0.1);border-color:var(--red);color:var(--red)}
</style></head><body><div class="box">
<h1>⚙️ SETTINGS</h1>
{% if saved %}<div class="ok">✅ Saved!</div>{% endif %}
<form method="POST" action="/settings">

<div class="sec">🎨 Theme</div>
<div class="theme-grid">
{% for t in themes %}
<div class="theme-opt">
  <input type="radio" name="theme" id="theme_{{t}}" value="{{t}}" {{'checked' if s_theme==t}} onchange="this.form.submit()">
  <label for="theme_{{t}}">{{theme_labels[t]}}</label>
</div>
{% endfor %}
</div>

<div class="sec">📊 Exchange APIs</div>
{% for ex, label in exchanges %}
<div class="exc-toggle">
  <div>
    <div class="exc-name">{{label}}</div>
    <div class="exc-status">{{'✅ Credentials set' if has_creds[ex] else '❌ Not configured'}}</div>
  </div>
  <div class="toggle-row">
    <input type="radio" name="{{ex}}_enabled" id="{{ex}}_on" value="on" style="display:none" {{'checked' if exc_enabled[ex]}}>
    <label for="{{ex}}_on" class="t-btn t-on" onclick="document.getElementById('{{ex}}_on').checked=true">🟢 ON</label>
    <input type="radio" name="{{ex}}_enabled" id="{{ex}}_off" value="off" style="display:none" {{'checked' if not exc_enabled[ex]}}>
    <label for="{{ex}}_off" class="t-btn t-off" onclick="document.getElementById('{{ex}}_off').checked=true">⚪ OFF</label>
  </div>
</div>
<label>{{label}} API Key <span class="opt">(blank = keep current)</span></label>
<input type="text" name="{{ex}}_key" placeholder="{{'••••'+api_keys[ex][-4:] if api_keys[ex] else 'Not set'}}" autocomplete="off" spellcheck="false">
<label>{{label}} Secret</label>
<input type="password" name="{{ex}}_secret" placeholder="{{'Currently set' if api_keys[ex] else 'Not set'}}">
{% if ex in ('kucoin','okx','bitget') %}
<label>{{label}} Passphrase</label>
<input type="text" name="{{ex}}_passphrase" placeholder="{{'Currently set' if passphrases[ex] else 'Not required'}}">
{% endif %}
{% endfor %}

<div class="sec">📱 Telegram</div>
<label>Bot Token <span class="opt">(blank = keep current)</span></label>
<input type="text" name="tg_token" placeholder="{{'••••'+tg_token[-6:] if tg_token else 'Not set'}}">
<label>Admin User ID <span class="opt">(blank = keep current)</span></label>
<input type="text" name="tg_chat_id" placeholder="{{tg_chat_id if tg_chat_id else 'Not set'}}">

<div class="sec">💵 Margin Sizing</div>
<div class="radio-row">
  <div class="radio-opt">
    <input type="radio" name="margin_mode" id="mm_fixed" value="fixed" {{'checked' if s_margin_mode=='fixed'}}>
    <label for="mm_fixed">Fixed $</label>
  </div>
  <div class="radio-opt">
    <input type="radio" name="margin_mode" id="mm_pct" value="percent" {{'checked' if s_margin_mode=='percent'}}>
    <label for="mm_pct">% of Balance</label>
  </div>
</div>
<label>Fixed margin per trade (USD)</label>
<input type="number" step="0.5" name="margin_usd" value="{{s_margin_usd}}">
<label>Margin as % of balance</label>
<input type="number" step="0.1" name="margin_percent" value="{{s_margin_percent}}">

<div class="sec">🛡 Margin Type</div>
<div class="radio-row">
  <div class="radio-opt">
    <input type="radio" name="margin_type" id="mt_cross" value="CROSSED" {{'checked' if s_margin_type=='CROSSED'}}>
    <label for="mt_cross">Cross</label>
  </div>
  <div class="radio-opt">
    <input type="radio" name="margin_type" id="mt_iso" value="ISOLATED" {{'checked' if s_margin_type=='ISOLATED'}}>
    <label for="mt_iso">Isolated</label>
  </div>
</div>
<div class="warn2">⚠️ Isolated caps loss to position margin. Cross shares margin across all positions.</div>

<div class="sec">🔒 Dev-Only Settings</div>
<div class="locked-box">
  <div class="locked-label">⚡ Leverage</div>
  <div class="locked-val">{{s_leverage}}x</div>
  <div class="locked-note">🔒 This setting is only available for developers and cannot be changed here.<br>
  Strategy is backtested and optimised for {{s_leverage}}x leverage.</div>
</div>
<div class="locked-box">
  <div class="locked-label">⏱ Coin Cooldown</div>
  <div class="locked-val">{{s_cooldown_min}} min</div>
  <div class="locked-note">🔒 Cooldown per coin after a position closes is locked by the developer.<br>
  Changing this affects strategy performance.</div>
</div>

<button type="submit">💾 SAVE SETTINGS</button>
</form>
<a class="back" href="/">← Back to dashboard</a>
</div></body></html>'''


HISTORY_HTML = '''<!DOCTYPE html><html><head>
<title>Trade History — G Max V1</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;padding:10px;font-size:12px}
h1{color:var(--green);font-size:15px;text-align:center;padding:8px 0;letter-spacing:1.5px}
.trade-row{background:var(--card);border:1px solid var(--border);border-radius:6px;padding:8px;margin-bottom:5px}
.row-top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:3px}
.coin{font-weight:bold}
.coin a{color:var(--text);text-decoration:none;border-bottom:1px dotted var(--blue)}
.g{color:var(--green)}.r{color:var(--red)}
.small{font-size:9.5px;color:var(--textdim2);margin-top:2px}
.tag{background:var(--border);border-radius:4px;padding:1px 5px;font-size:8.5px;color:var(--blue);margin-left:4px}
a.back{display:block;text-align:center;color:var(--blue);margin:12px 0;font-size:11px;text-decoration:none}
.empty{text-align:center;color:var(--faint);padding:20px}
.pager{display:flex;gap:6px;margin:10px 0}
.pager a{flex:1;text-align:center;padding:8px;background:var(--card);border:1px solid var(--border);
  border-radius:6px;color:var(--blue);text-decoration:none;font-size:11px;font-weight:bold}
.pager a.active{border-color:var(--green);color:var(--green);background:rgba(var(--green-rgb),0.07)}
.page-info{text-align:center;color:var(--textdim2);font-size:10px;margin-bottom:8px}
</style></head><body>
<h1>📜 TRADE HISTORY</h1>
<a class="back" href="/">← Back to dashboard</a>
<div class="page-info">Showing {{start+1}}–{{end}} of {{total}} trades</div>
<div class="pager">
  {% if page > 1 %}<a href="/history?page={{page-1}}">← Newer</a>{% endif %}
  {% if has_more %}<a href="/history?page={{page+1}}">Older →</a>{% endif %}
</div>
{% if trades %}
{% for t in trades %}
<div class="trade-row">
  <div class="row-top">
    <span class="coin"><a href="/coin/{{t.symbol}}">{{t.name}}</a>
      <span class="tag">{{t.exchange_label}}</span></span>
    <span class="{{'g' if t.pnl>=0 else 'r'}}">{{'✅' if t.pnl>0 else ('❌' if t.pnl<0 else '➖')}} ${{'%+.3f'|format(t.pnl)}}</span>
  </div>
  <div class="small">{{'LONG' if t.side=='buy' else 'SHORT'}} · entry {{t.entry}} → exit {{t.exit}} · {{t.closed}} · {{t.reason}}</div>
</div>
{% endfor %}
{% else %}<div class="empty">No trades yet</div>{% endif %}
<div class="pager">
  {% if page > 1 %}<a href="/history?page={{page-1}}">← Newer</a>{% endif %}
  {% if has_more %}<a href="/history?page={{page+1}}">Older →</a>{% endif %}
</div>
</body></html>'''

COIN_HTML = '''<!DOCTYPE html><html><head>
<title>{{coin}} — G Max V1</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;padding:10px;font-size:12px}
h1{color:var(--green);font-size:18px;text-align:center;padding:10px 0 4px;font-weight:900;letter-spacing:1px}
a.back{display:block;text-align:center;color:var(--blue);margin-bottom:10px;font-size:11px;text-decoration:none}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:8px}
.card{background:var(--card);border:1px solid var(--border);border-radius:7px;padding:8px 6px;text-align:center}
.lbl{font-size:9px;color:var(--textdim2);text-transform:uppercase;letter-spacing:1px;font-weight:900}
.val{font-size:16px;font-weight:900;margin-top:3px}
.g{color:var(--green)}.r{color:var(--red)}.y{color:var(--yellow)}.b{color:var(--blue)}
.section{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:9px;margin-bottom:8px}
.sh{font-size:10px;color:var(--green);text-transform:uppercase;letter-spacing:1.5px;font-weight:900;
  margin-bottom:7px;padding-bottom:5px;border-bottom:1px solid var(--border)}
.trade-row{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:7px 8px;margin-bottom:5px;font-size:11px}
.row-top{display:flex;justify-content:space-between;align-items:center}
.small{font-size:9.5px;color:var(--textdim2);margin-top:2px}
.empty{text-align:center;color:var(--faint);padding:14px;font-size:11px}
.tag{background:var(--border);border-radius:4px;padding:2px 6px;font-size:9.5px;color:var(--blue);font-weight:900}
</style></head><body>
<h1>💎 {{coin}}/USDT</h1>
<a class="back" href="/">← Back to dashboard</a>
<div class="section">
<div class="sh">📊 Stats — {{coin}}</div>
<div class="grid">
<div class="card"><div class="lbl">Today PnL</div>
  <div class="val {{'g' if stats.today_pnl>=0 else 'r'}}">${{'%+.3f'|format(stats.today_pnl)}}</div></div>
<div class="card"><div class="lbl">Total PnL</div>
  <div class="val {{'g' if stats.total_pnl>=0 else 'r'}}">${{'%+.3f'|format(stats.total_pnl)}}</div></div>
<div class="card"><div class="lbl">Win Rate</div>
  <div class="val y">{{'%.0f'|format(stats.wr) if stats.total>0 else '--'}}{{'%' if stats.total>0}}</div></div>
<div class="card"><div class="lbl">Total Trades</div><div class="val b">{{stats.total}}</div></div>
</div>
</div>
<div class="section">
<div class="sh">📜 Trade History — {{coin}}</div>
{% if trades %}
{% for t in trades %}
<div class="trade-row">
  <div class="row-top">
    <span><b>{{'LONG' if t.side=='buy' else 'SHORT'}}</b> <span class="tag">{{t.exchange_label}}</span></span>
    <span class="{{'g' if t.pnl>=0 else 'r'}}">{{'✅' if t.pnl>0 else ('❌' if t.pnl<0 else '➖')}} ${{'%+.3f'|format(t.pnl)}}</span>
  </div>
  <div class="small">entry {{t.entry}} → exit {{t.exit}} · {{t.closed}} · {{t.reason}}</div>
</div>
{% endfor %}
{% else %}<div class="empty">No trades for this coin yet</div>{% endif %}
</div>
</body></html>'''

DATA_HTML = '''<!DOCTYPE html><html><head>
<title>Data Center — G Max V1</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;padding:10px;font-size:12px}
h1{color:var(--green);font-size:16px;text-align:center;padding:8px 0 4px;font-weight:900;letter-spacing:1px}
a.back{display:block;text-align:center;color:var(--blue);margin-bottom:10px;font-size:11px;text-decoration:none}
.grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:5px;margin-bottom:8px}
.card{background:var(--card);border:1px solid var(--border);border-radius:7px;padding:8px 6px;text-align:center}
.lbl{font-size:8.5px;color:var(--textdim2);text-transform:uppercase;letter-spacing:0.5px;font-weight:900}
.val{font-size:14px;font-weight:900;margin-top:3px}
.g{color:var(--green)}.r{color:var(--red)}.y{color:var(--yellow)}.b{color:var(--blue)}
.section{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:10px;margin-bottom:8px}
.sh{font-size:10px;color:var(--green);text-transform:uppercase;letter-spacing:1.5px;font-weight:900;
  margin-bottom:8px;padding-bottom:5px;border-bottom:1px solid var(--border)}
.dc-row{display:flex;align-items:flex-end;gap:3px;height:80px;margin-bottom:4px;overflow-x:auto}
.dc-col{flex:1;min-width:16px;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%}
.dc-bar{width:70%;border-radius:2px 2px 0 0;min-height:2px}
.dc-lbl{font-size:6.5px;color:var(--faint);text-align:center}
.eq-row{display:flex;align-items:flex-end;gap:1px;height:60px;margin-bottom:4px}
.eq-bar{flex:1;min-width:2px;border-radius:1px 1px 0 0}
.gl-row{display:flex;align-items:center;gap:6px;padding:4px 0;font-size:10px}
.gl-name{min-width:44px;font-weight:900}
.gl-name a{color:var(--text);text-decoration:none}
.gl-track{flex:1;background:var(--bg);border-radius:3px;height:12px;overflow:hidden}
.gl-fill{height:100%;border-radius:3px}
.gl-fill.g{background:rgba(var(--green-rgb),0.4)}
.gl-fill.r{background:rgba(var(--red-rgb),0.4)}
.gl-val{min-width:52px;text-align:right;font-size:9.5px;font-weight:900}
.stat-row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border);font-size:11px}
.stat-row:last-child{border:none}
.empty{text-align:center;color:var(--faint);padding:14px;font-size:11px}
</style></head><body>
<h1>📊 DATA CENTER</h1>
<a class="back" href="/">← Back to dashboard</a>
<div class="grid">
<div class="card"><div class="lbl">Total Trades</div><div class="val b">{{a.total_trades}}</div></div>
<div class="card"><div class="lbl">Max Drawdown</div><div class="val r">${{'%.2f'|format(a.max_drawdown)}}</div></div>
<div class="card"><div class="lbl">Drawdown %</div><div class="val r">{{'%.1f'|format(a.max_drawdown_pct)}}%</div></div>
<div class="card"><div class="lbl">Avg Win</div><div class="val g">${{'%.2f'|format(a.avg_win)}}</div></div>
<div class="card"><div class="lbl">Avg Loss</div><div class="val r">${{'%.2f'|format(a.avg_loss)}}</div></div>
<div class="card"><div class="lbl">Avg R:R</div><div class="val y">{{a.avg_rr}}</div></div>
<div class="card"><div class="lbl">Max Win Streak</div><div class="val g">{{a.max_win_streak}}</div></div>
<div class="card"><div class="lbl">Max Loss Streak</div><div class="val r">{{a.max_loss_streak}}</div></div>
<div class="card"><div class="lbl">Cur Streak</div>
  <div class="val {{'g' if a.cur_streak_type=='win' else 'r'}}">{{a.cur_streak}} {{a.cur_streak_type}}</div></div>
</div>
<div class="section">
<div class="sh">🏆 Best / Worst Trade</div>
{% if a.best_trade %}
<div class="stat-row"><span>Best — {{a.best_trade.symbol}}</span><b class="g">${{'%+.3f'|format(a.best_trade.pnl)}}</b></div>
<div class="stat-row"><span>Worst — {{a.worst_trade.symbol}}</span><b class="r">${{'%+.3f'|format(a.worst_trade.pnl)}}</b></div>
{% else %}<div class="empty">No trades yet</div>{% endif %}
</div>
<div class="section">
<div class="sh">📈 Equity Curve</div>
{% if eq_bars %}
<div class="eq-row">
{% for b in eq_bars %}
<div class="eq-bar" style="height:{{b.pct}}%;background:{{'var(--green)' if b.val>=0 else 'var(--red)'}}"></div>
{% endfor %}
</div>
<div class="stat-row"><span>Running total</span>
  <b class="{{'g' if a.equity_curve[-1]>=0 else 'r'}}">${{'%+.3f'|format(a.equity_curve[-1])}}</b></div>
{% else %}<div class="empty">No trades yet</div>{% endif %}
</div>
<div class="section">
<div class="sh">📆 Daily P&L — last 14 days</div>
<div class="dc-row">
{% for d in daily_pnl %}
<div class="dc-col">
  <div class="dc-bar" style="height:{{d.bar_pct}}%;background:{{'var(--green)' if d.pnl>=0 else 'var(--red)'}}"></div>
</div>
{% endfor %}
</div>
<div style="display:flex;gap:3px">
{% for d in daily_pnl %}<div class="dc-lbl" style="flex:1;min-width:16px">{{d.label}}</div>{% endfor %}
</div>
</div>
<div class="section">
<div class="sh">🟢 Top 10 Profitable Coins</div>
{% if top_gainers %}
{% for g in top_gainers %}
<div class="gl-row">
  <span class="gl-name"><a href="/coin/{{g.symbol}}">{{g.name}}</a></span>
  <div class="gl-track"><div class="gl-fill g" style="width:{{g.bar_pct}}%"></div></div>
  <span class="gl-val g">${{'%+.2f'|format(g.pnl)}}</span>
</div>
{% endfor %}
{% else %}<div class="empty">No profitable coins yet</div>{% endif %}
</div>
<div class="section">
<div class="sh">🔴 Top 10 Losing Coins</div>
{% if top_losers %}
{% for l in top_losers %}
<div class="gl-row">
  <span class="gl-name"><a href="/coin/{{l.symbol}}">{{l.name}}</a></span>
  <div class="gl-track"><div class="gl-fill r" style="width:{{l.bar_pct}}%"></div></div>
  <span class="gl-val r">${{'%+.2f'|format(l.pnl)}}</span>
</div>
{% endfor %}
{% else %}<div class="empty">No losing coins yet</div>{% endif %}
</div>
</body></html>'''


CONTACT_HTML = '''<!DOCTYPE html><html><head>
<title>Contact Us — G Max V1</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'Courier New',monospace;
  display:flex;flex-direction:column;min-height:100vh}
.header{background:var(--card);border-bottom:1px solid var(--border);
  padding:10px 12px;display:flex;align-items:center;gap:10px;flex-shrink:0}
.header a{color:var(--blue);text-decoration:none;font-size:11px}
.header-title{flex:1;font-size:13px;font-weight:bold;color:var(--text)}
.header-sub{font-size:9px;color:var(--textdim2)}
.wrap{flex:1;padding:16px 12px;display:flex;flex-direction:column;gap:10px}
.intro{font-size:11px;color:var(--textdim);line-height:1.6;margin-bottom:6px;text-align:center}
.contact-card{background:var(--card);border:1px solid var(--border);border-radius:10px;
  padding:14px;display:flex;align-items:center;gap:12px;text-decoration:none;color:var(--text)}
.contact-icon{font-size:22px;width:36px;text-align:center;flex-shrink:0}
.contact-info{flex:1;min-width:0}
.contact-platform{font-size:12px;font-weight:bold;color:var(--text)}
.contact-handle{font-size:10.5px;color:var(--blue);word-break:break-all;margin-top:2px}
.contact-arrow{color:var(--faint);font-size:14px;flex-shrink:0}
.empty{text-align:center;color:var(--faint);padding:40px 20px;font-size:11px}
</style></head>
<body data-theme="{{theme}}">
<div class="header">
  <a href="/">← Back</a>
  <div>
    <div class="header-title">💬 Contact Us</div>
    <div class="header-sub">Paqu Trading · G MAX V1</div>
  </div>
</div>

<div class="wrap">
  <div class="intro">Need help? Reach us directly on any of these — we usually reply fast.</div>

  {% if not contacts %}
  <div class="empty">No contact info set up yet.<br>Please check back soon.</div>
  {% else %}
    {% for c in contacts %}
    <a class="contact-card" href="{{c.link}}" target="_blank" rel="noopener">
      <div class="contact-icon">{{c.icon}}</div>
      <div class="contact-info">
        <div class="contact-platform">{{c.platform}}</div>
        <div class="contact-handle">{{c.handle}}</div>
      </div>
      <div class="contact-arrow">›</div>
    </a>
    {% endfor %}
  {% endif %}
</div>

</body></html>'''

# ── Inject theme CSS + data-theme into all templates ──────
for _tpl_name in ('SETUP_HTML','DASH_HTML','SETTINGS_HTML','HISTORY_HTML','COIN_HTML','DATA_HTML','CONTACT_HTML'):
    _tpl = globals()[_tpl_name]
    _tpl = _tpl.replace('<style>', '<style>' + THEME_CSS, 1)
    _tpl = _tpl.replace('<body>', '<body data-theme="{{theme}}">', 1)
    globals()[_tpl_name] = _tpl

# ══════════════════════════════════════════════════════════
#  FLASK APP
# ══════════════════════════════════════════════════════════
app = Flask(__name__)

LBLOCK = ('<!DOCTYPE html><html><head>''<meta name="viewport" content="width=device-width,initial-scale=1">''<style>*{margin:0;padding:0;box-sizing:border-box}''body{background:#070d1a;color:#fff;font-family:Courier New,monospace;''display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}''div.box{background:#0d1526;border:1px solid #ff4060;border-radius:12px;padding:24px;max-width:400px;text-align:center}''h1{color:#ff4060;font-size:16px;margin-bottom:12px}''p{color:#aabbdd;font-size:12px;line-height:2;margin-bottom:16px}''a{color:#4499ff;text-decoration:none;display:block;margin-top:6px}''</style></head><body><div class="box">''<h1>🔒 G MAX V1 — License Invalid</h1>''<p>{msg}</p>''<p>Signals stopped. Bot locked.</p>''{contact_links}''</div></body></html>'
)

def _contact_links_html():
    """Renders each configured Contact Us entry as a link for the lock screen."""
    contacts = _fetch_contact_info()
    if not contacts:
        return '<a href="https://t.me/PaquTrading">📱 Contact support</a>'
    return ''.join(
        f'<a href="{c.get("link","#")}">{c.get("icon","💬")} {c.get("platform","Contact")}</a>'
        for c in contacts
    )

@app.route('/')
def dashboard():
    if not is_configured():
        return redirect('/setup')

    # Token check — only block on revoked/expired/device_locked/code_modified
    cfg_lic = load_config()
    token_status = cfg_lic.get('token_status', 'pending')
    license_expiry = cfg_lic.get('license_expiry', '')
    reason = _license_cache.get('reason','')
    if reason in ('expired','revoked','device_locked','code_modified'):
        msgs = {
            'expired'       : '\u23f0 Your license has expired. Contact support to renew.',
            'revoked'       : '\u274c Access revoked. Contact support.',
            'device_locked' : '\U0001f512 Bot already running on another device. Contact support to reset.',
            'code_modified' : '\u26a0\ufe0f Code has been modified. Please contact customer service below.',
        }
        msg = msgs.get(reason, '\u274c License invalid. Contact support.')
        return LBLOCK.format(msg=msg, contact_links=_contact_links_html()), 403



    cfg = load_config()
    ov  = _overall_stats()

    # Exchange status chips
    exchange_chips = []
    for ex in EXCHANGES:
        has_k = exchange_has_credentials(ex)
        enabled = cfg.get(f'{ex}_enabled', True)
        if not has_k:
            continue
        st = 'off' if not enabled else state['exchange_status'].get(ex, 'ok')
        exchange_chips.append((ex, EXCHANGE_LABELS[ex], st))

    # Warnings
    warnings = []
    for key, pos in state['open_positions'].items():
        ex = pos['exchange']
        sym = pos['symbol']
        # (warnings about unlisted coins come from place_order_on_exchange at trade time)

    # Positions
    positions = []
    for key, pos in state['open_positions'].items():
        positions.append({
            'symbol'       : pos['symbol'],
            'name'         : display_name(pos['symbol']),
            'exchange'     : pos['exchange'],
            'exchange_label': EXCHANGE_LABELS.get(pos['exchange'], pos['exchange']),
            'side'         : pos['side'],
            'entry'        : pos['entry'],
            'tp'           : pos['tp'],
            'sl'           : pos['sl'],
            'leverage'     : pos['leverage'],
            'opened'       : pos.get('opened','--'),
        })

    # Top coins
    top_coins = []
    for name, d in _coin_stats_ranked()[:10]:
        top_coins.append({'name':name,'symbol':d.get('symbol') or name,
                          'wr':d['wr'],'wins':d['wins'],'losses':d['losses'],'pnl':d['total_pnl']})

    # Recent trades (20)
    recent_trades = []
    for t in reversed(state['trades'][-20:]):
        recent_trades.append({
            'symbol'       : t['symbol'],
            'name'         : display_name(t['symbol']),
            'exchange_label': EXCHANGE_LABELS.get(t.get('exchange',''), t.get('exchange','')),
            'side'         : t['side'],
            'pnl'          : t['pnl'],
            'closed'       : t.get('closed',''),
            'reason'       : t.get('reason',''),
        })

    wr = f"{ov['wr']:.0f}%" if (ov['wins']+ov['losses']) else '--'
    pf_disp = _pf_display(ov['pf'])

    # Fetch balances per exchange (cached 60s)
    bals = _fetch_balances()
    balance_items = [(EXCHANGE_LABELS.get(ex, ex), round(bal, 2)) for ex, bal in bals.items() if bal > 0]
    total_bal = round(sum(bals.values()), 2)

    return render_template_string(DASH_HTML,
        exchange_chips=exchange_chips,
        exchange_labels=EXCHANGE_LABELS,
        last_signal=state['last_signal'],
        display_name=display_name,
        warnings=warnings,
        today_pnl=ov['today_pnl'], total_pnl=ov['total_pnl'],
        wr=wr, pf_display=pf_disp,
        pos_count=len(positions), trade_count=len(state['trades']),
        positions=positions, top_coins=top_coins,
        recent_trades=recent_trades,
        signal_count=state['signal_count'],
        start_time=state['start_time'], now=_dt(),
        total_balance=total_bal, balance_items=balance_items,
        token_status=token_status,
        license_expiry=license_expiry,
        update_available=bool(_pending_update.get('version')),
        latest_version=_pending_update.get('version', ''),
        update_command=_update_command(),
        whats_new=_pending_update.get('whats_new', ''),
        theme=current_theme())

@app.route('/setup', methods=['GET','POST'])
def setup():
    if request.method == 'GET':
        return render_template_string(SETUP_HTML,
            exchanges=[(ex, EXCHANGE_LABELS[ex]) for ex in EXCHANGES],
            error=None, theme=current_theme())

    data = {}
    for ex in EXCHANGES:
        key    = request.form.get(f'{ex}_key','').strip()
        secret = request.form.get(f'{ex}_secret','').strip()
        pp     = request.form.get(f'{ex}_passphrase','').strip()
        if key:    data[f'{ex}_key'] = key
        if secret: data[f'{ex}_secret'] = secret
        if pp:     data[f'{ex}_passphrase'] = pp
        data[f'{ex}_enabled'] = True

    tg_token    = request.form.get('tg_token','').strip()
    tg_token   = request.form.get('tg_token','').strip()
    tg_chat_id = request.form.get('tg_chat_id','').strip()

    # Validation
    has_exchange = any(request.form.get(f'{ex}_key','').strip() for ex in EXCHANGES)
    if not has_exchange:
        return render_template_string(SETUP_HTML,
            exchanges=[(ex, EXCHANGE_LABELS[ex]) for ex in EXCHANGES],
            error="At least one Exchange API key is required", theme=current_theme())
    if not tg_token or not tg_chat_id:
        return render_template_string(SETUP_HTML,
            exchanges=[(ex, EXCHANGE_LABELS[ex]) for ex in EXCHANGES],
            error="Telegram Bot Token and Admin User ID are required", theme=current_theme())

    if tg_token:   data['tg_token']   = tg_token
    if tg_chat_id: data['tg_chat_id'] = tg_chat_id
    # Save everything locally immediately — no server wait
    save_config_data(data)
    save_config_data({'license_valid': False, 'license_expiry': '', 'token_status': 'pending'})
    # Start bot right away — validation happens in background
    start_bot()
    return redirect('/')

@app.route('/settings', methods=['GET','POST'])
def settings_page():
    if request.method == 'POST':
        data = {}
        # Theme
        theme = request.form.get('theme','classic')
        if theme in THEMES: data['theme'] = theme

        # Exchange toggles + credentials
        for ex in EXCHANGES:
            data[f'{ex}_enabled'] = request.form.get(f'{ex}_enabled','off') == 'on'
            key    = request.form.get(f'{ex}_key','').strip()
            secret = request.form.get(f'{ex}_secret','').strip()
            pp     = request.form.get(f'{ex}_passphrase','').strip()
            if key:    data[f'{ex}_key']        = key
            if secret: data[f'{ex}_secret']     = secret
            if pp:     data[f'{ex}_passphrase'] = pp

        # Telegram
        tg_token   = request.form.get('tg_token','').strip()
        tg_chat_id = request.form.get('tg_chat_id','').strip()
        if tg_token:   data['tg_token']   = tg_token
        if tg_chat_id: data['tg_chat_id'] = tg_chat_id

        # Margin
        try:
            data['margin_usd']     = float(request.form.get('margin_usd', 10.0))
            data['margin_percent'] = float(request.form.get('margin_percent', 2.0))
            data['margin_mode']    = request.form.get('margin_mode','fixed')
            data['margin_type']    = request.form.get('margin_type','CROSSED')
            if data['margin_mode'] not in ('fixed','percent'): data['margin_mode']='fixed'
            if data['margin_type'] not in ('CROSSED','ISOLATED'): data['margin_type']='CROSSED'
        except (ValueError, TypeError): pass

        save_config_data(data)
        return redirect('/settings?saved=1')

    cfg = load_config()
    s   = get_settings()
    return render_template_string(SETTINGS_HTML,
        s_theme=current_theme(),
        s_leverage=s['leverage'],
        s_cooldown_min=s['cooldown_min'],
        s_margin_mode=s['margin_mode'],
        s_margin_usd=s['margin_usd'],
        s_margin_percent=s['margin_percent'],
        s_margin_type=s['margin_type'],
        themes=THEMES, theme_labels=THEME_LABELS,
        exchanges=[(ex, EXCHANGE_LABELS[ex]) for ex in EXCHANGES],
        has_creds={ex: exchange_has_credentials(ex) for ex in EXCHANGES},
        exc_enabled={ex: cfg.get(f'{ex}_enabled', True) for ex in EXCHANGES},
        api_keys={ex: cfg.get(f'{ex}_key','') for ex in EXCHANGES},
        passphrases={ex: cfg.get(f'{ex}_passphrase','') for ex in EXCHANGES},
        tg_token=cfg.get('tg_token',''),
        tg_chat_id=cfg.get('tg_chat_id',''),
        saved=request.args.get('saved')=='1',
        theme=current_theme())

@app.route('/history')
def history_page():
    PAGE_SIZE = 50
    page = int(request.args.get('page', 1))
    all_trades = list(reversed(state['trades']))
    total = len(all_trades)
    start = (page-1)*PAGE_SIZE
    end   = min(start+PAGE_SIZE, total)
    page_trades = all_trades[start:end]

    trades = []
    for t in page_trades:
        trades.append({
            'symbol'       : t['symbol'],
            'name'         : display_name(t['symbol']),
            'exchange_label': EXCHANGE_LABELS.get(t.get('exchange',''), t.get('exchange','')),
            'side'         : t['side'],
            'pnl'          : t['pnl'],
            'entry'        : t.get('entry',''),
            'exit'         : t.get('exit',''),
            'closed'       : t.get('closed',''),
            'reason'       : t.get('reason',''),
        })
    return render_template_string(HISTORY_HTML,
        trades=trades, page=page, total=total,
        start=start, end=end, has_more=end<total,
        theme=current_theme())

@app.route('/coin/<symbol>')
def coin_detail(symbol):
    symbol = symbol.upper()
    ss  = _symbol_stats()
    key = display_name(symbol)
    d   = ss.get(key, {'wins':0,'losses':0,'total_pnl':0.0,'today_pnl':0.0,'wr':0,'total':0})

    today = _today_str()
    today_pnl = sum(t['pnl'] for t in state['trades']
                    if t.get('status')=='closed' and t['symbol']==symbol
                    and t.get('closed_date')==today)
    d['today_pnl'] = today_pnl

    trades = []
    for t in reversed(state['trades']):
        if t['symbol'] != symbol: continue
        trades.append({
            'side'         : t['side'],
            'pnl'          : t['pnl'],
            'entry'        : t.get('entry',''),
            'exit'         : t.get('exit',''),
            'closed'       : t.get('closed',''),
            'reason'       : t.get('reason',''),
            'exchange_label': EXCHANGE_LABELS.get(t.get('exchange',''), t.get('exchange','')),
        })
    return render_template_string(COIN_HTML,
        coin=display_name(symbol), symbol=symbol,
        stats=type('S',(),d)(), trades=trades,
        theme=current_theme())

@app.route('/data')
def data_center():
    a = _advanced_stats()
    eq_bars = []
    if a['equity_curve']:
        max_abs = max(abs(v) for v in a['equity_curve']) or 1
        for v in a['equity_curve']:
            eq_bars.append({'val':v,'pct':max(2,round(abs(v)/max_abs*100))})

    daily = _daily_pnl_series(14)
    max_abs_day = max((abs(d['pnl']) for d in daily), default=1) or 1
    for d in daily:
        d['bar_pct'] = min(100, round(abs(d['pnl'])/max_abs_day*100))

    ss = _symbol_stats()
    ranked = sorted(ss.items(), key=lambda x:x[1]['total_pnl'], reverse=True)
    gainers = [r for r in ranked if r[1]['total_pnl']>0][:10]
    losers  = [r for r in ranked if r[1]['total_pnl']<0][-10:][::-1]
    max_gain = max((d['total_pnl'] for _,d in gainers), default=1) or 1
    max_loss = max((abs(d['total_pnl']) for _,d in losers), default=1) or 1

    top_gainers=[{'symbol':sym,'name':display_name(sym),'pnl':d['total_pnl'],
                  'bar_pct':min(100,round(d['total_pnl']/max_gain*100))} for sym,d in gainers]
    top_losers=[{'symbol':sym,'name':display_name(sym),'pnl':d['total_pnl'],
                 'bar_pct':min(100,round(abs(d['total_pnl'])/max_loss*100))} for sym,d in losers]

    return render_template_string(DATA_HTML,
        a=type('A',(),a)(), eq_bars=eq_bars, daily_pnl=daily,
        top_gainers=top_gainers, top_losers=top_losers,
        theme=current_theme())

@app.route('/close/<ex>/<symbol>', methods=['POST'])
def close_route(ex, symbol):
    key = f"{ex}:{symbol.upper()}"
    pos = state['open_positions'].get(key)
    if pos:
        try:
            client = _build_ccxt(ex)
            if client:
                sym_ccxt = _get_symbol_ccxt(ex, symbol.upper())
                close_side = 'sell' if pos['side']=='buy' else 'buy'
                client.create_order(sym_ccxt, 'market', close_side, pos['qty'],
                                    params={'reduceOnly':True})
                ticker = client.fetch_ticker(sym_ccxt)
                exit_price = float(ticker['last'])
                _record_close(ex, symbol.upper(), exit_price, 'manual_close')
        except Exception as e:
            log.info(f"Manual close error: {e}")
    return redirect('/')

@app.route('/api/state')
def api_state():
    ov = _overall_stats()
    return jsonify({
        'signal_count'   : state['signal_count'],
        'open_positions' : len(state['open_positions']),
        'total_pnl'      : state['total_pnl'],
        'wins'           : ov['wins'],
        'losses'         : ov['losses'],
        'last_signal'    : state['last_signal'],
        'exchange_status': state['exchange_status'],
    })

@app.route('/contact')
def contact_page():
    if not is_configured(): return redirect('/setup')
    if not license_check():
        return redirect('/')
    contacts = _fetch_contact_info()
    return render_template_string(CONTACT_HTML,
        contacts=contacts,
        theme=current_theme())

# ── Main ────────────────────────────────────────────────────
if __name__ == '__main__':
    if is_configured():
        start_bot()
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

