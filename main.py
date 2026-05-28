import os
import time
import pandas as pd
from pybit.unified_trading import HTTP
from datetime import datetime, timezone

# ==========================================================
# CONFIGURAZIONE PRINCIPALE - CAMBIA QUI
# ==========================================================
SYMBOL = "BILLUSDT"
BASE_QTY = 100
PERC_PAUSE = 2.5

GRID_MULTIPLIERS = [1, 1, 1, 2, 2, 3, 4, 5, 6, 7, 9, 11, 13]

current_mode = "AGGRESSIVE"
COOLDOWN = 20

# ==========================================================
# DECIMALI (modifica manualmente secondo la coppia)
# ==========================================================
PRICE_DECIMALS = 5      # LABUSDT → 4 
QTY_DECIMALS = 0        # di solito 2 o 4

# ==========================================================
# VARIABILI DI STATO
# ==========================================================
pause_until_next_candle = False
last_candle_ts = 0
last_trade_time = 0
last_tp_price = 0.0
last_tp_update_time = 0

session = HTTP(testnet=False, 
               api_key=os.environ.get("BYBIT_API_KEY"), 
               api_secret=os.environ.get("BYBIT_API_SECRET"))

# ==========================================================
# FUNZIONI
# ==========================================================

def round_price(price):
    return round(price, PRICE_DECIMALS)

def round_qty(qty):
    return round(qty, QTY_DECIMALS)


def cancel_all_orders():
    try:
        session.cancel_all_orders(category="linear", symbol=SYMBOL)
        time.sleep(1.0)
        print("✅ Tutti gli ordini cancellati")
        return True
    except Exception as e:
        print(f"Errore cancel all: {e}")
        return False


def close_position():
    try:
        pos = session.get_positions(category="linear", symbol=SYMBOL)["result"]["list"][0]
        size = float(pos.get("size", 0))
        if size == 0:
            return False
            
        side = "Sell" if size > 0 else "Buy"
        session.place_order(
            category="linear", 
            symbol=SYMBOL, 
            side=side, 
            orderType="Market", 
            qty=str(abs(size)), 
            reduceOnly=True
        )
        print(f"🔴 POSIZIONE CHIUSA A MERCATO | Size: {size}")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"Errore chiusura posizione: {e}")
        return False


def get_current_price():
    try:
        ticker = session.get_tickers(category="linear", symbol=SYMBOL)
        return float(ticker['result']['list'][0]['lastPrice'])
    except:
        return None


def get_volatility_data(symbol):
    try:
        data = session.get_kline(category="linear", symbol=symbol, interval="240", limit=42)
        df = pd.DataFrame(data['result']['list'], 
                         columns=['ts', 'open', 'high', 'low', 'close', 'vol', 'turnover'])
        
        df['close'] = df['close'].astype(float)
        df['low'] = df['low'].astype(float)
        df['ts'] = df['ts'].astype(int)
        
        sma = df['close'].rolling(window=40).mean()
        std = df['close'].rolling(window=40).std()
        lower_band = sma - (std * 2)
        
        bb_width_percent = ((sma.iloc[-1] - lower_band.iloc[-1]) / sma.iloc[-1]) * 100
        
        return {
            'ts': df['ts'].iloc[-1],
            'bb_width': round(bb_width_percent, 2),
            'lower_band': round(lower_band.iloc[-1], 4),
        }
    except Exception as e:
        print(f"Errore Kline: {e}")
        return None


def get_spacing(i, mode):
    if mode == "AGGRESSIVE":
        if i <= 3:   return 1.0
        elif i <= 6: return 1.2
        elif i <= 9: return 1.5
        else:        return 1.8
    else:
        if i <= 3:   return 2.0
        elif i <= 6: return 2.4
        elif i <= 9: return 2.8
        else:        return 3.2


def should_check_candle():
    now_utc = datetime.now(timezone.utc)
    return (now_utc.hour % 4 == 0 and now_utc.minute == 0 and 5 <= now_utc.second <= 25)


# ==========================================================
# AVVIO BOT
# ==========================================================
print("🚀 BOT MASTER - Griglia a Fasce Corretta (v2.4)")
print(f"Symbol: {SYMBOL} | BASE_QTY: {BASE_QTY} | PERC_PAUSE: {PERC_PAUSE}% | Price Decimals: {PRICE_DECIMALS}\n")

while True:
    try:
        now = time.time()
        price = get_current_price()

        pos_data = session.get_positions(category="linear", symbol=SYMBOL)["result"]["list"][0]
        size = float(pos_data["size"])
        avg_price = float(pos_data.get("avgPrice", 0))

        active_orders = session.get_open_orders(category="linear", symbol=SYMBOL)["result"]["list"]

        # ==================== CONTROLLO CANDELA 4H ====================
        if should_check_candle():
            vol_data = get_volatility_data(SYMBOL)
            if vol_data and vol_data['ts'] != last_candle_ts:
                print(f"📊 Candela 4H → {datetime.now().strftime('%H:%M:%S')} | BB Width: {vol_data['bb_width']}%")

                new_mode = "CONSERVATIVE" if vol_data.get('bb_width', 0) > 40 else "AGGRESSIVE"
                if new_mode != current_mode:
                    print(f"🔄 CAMBIO MODALITÀ → {new_mode}")
                    current_mode = new_mode

                if price and vol_data.get('lower_band'):
                    distance = ((price - vol_data['lower_band']) / vol_data['lower_band']) * 100
                    
                    previous_pause = pause_until_next_candle
                    pause_until_next_candle = (distance <= PERC_PAUSE)
                    
                    print(f"{'⏸️ PAUSA ATTIVATA' if pause_until_next_candle else '▶️ Pausa disattivata'} | Distanza: {distance:.2f}%")

                    if pause_until_next_candle and not previous_pause:
                        print("🚨 PAUSA ATTIVATA → CHIUSURA FORZATA DI POSIZIONE E ORDINI")
                        cancel_all_orders()
                        close_position()
                        last_trade_time = now + 40

                last_candle_ts = vol_data['ts']

        # ==================== GESTIONE TP ====================
        if size > 0:
            tp_percent = 1.20 if current_mode == "CONSERVATIVE" else 0.90
            target_tp = round_price(avg_price * (1 + tp_percent / 100))

            if (abs(target_tp - last_tp_price) > 0.0005) and (now - last_tp_update_time > 12):
                
                tp_orders = [o for o in active_orders 
                            if o.get("side") == "Sell" 
                            and o.get("orderType") == "Limit"
                            and o.get("reduceOnly") is True]

                update_needed = False
                if not tp_orders:
                    update_needed = True
                else:
                    current_tp = float(tp_orders[0]["price"])
                    if abs(current_tp - target_tp) > 0.001:
                        update_needed = True
                        try:
                            session.cancel_order(category="linear", symbol=SYMBOL, orderId=tp_orders[0]["orderId"])
                        except:
                            pass

                if update_needed:
                    session.place_order(
                        category="linear", 
                        symbol=SYMBOL, 
                        side="Sell", 
                        orderType="Limit",
                        qty=str(size), 
                        price=str(target_tp), 
                        reduceOnly=True
                    )
                    last_tp_price = target_tp
                    last_tp_update_time = now
                    print(f"🎯 TP impostato → {target_tp} | Avg: {avg_price:.4f}")

        # ==================== NUOVA ENTRATA ====================
        elif size == 0 and (now - last_trade_time > COOLDOWN):
            if pause_until_next_candle:
                print(f"⏸️ IN PAUSA | Prezzo: {price:.4f}")
                cancel_all_orders()
            else:
                print(f"🟢 NUOVA ENTRATA @ {price:.4f} | Mode: {current_mode}")
                cancel_all_orders()
                time.sleep(1.5)

                session.place_order(
                    category="linear", symbol=SYMBOL, side="Buy", 
                    orderType="Market", qty=str(BASE_QTY)
                )
                time.sleep(2.5)

                new_pos = session.get_positions(category="linear", symbol=SYMBOL)["result"]["list"][0]
                if float(new_pos["size"]) > 0:
                    avg = float(new_pos["avgPrice"])
                    print(f"✅ Entrata confermata @ {avg:.4f}")

                    accumulated_drop = 0
                    for i in range(1, len(GRID_MULTIPLIERS)):
                        spacing = get_spacing(i, current_mode)
                        accumulated_drop += spacing
                        entry_price = round_price(avg * (1 - accumulated_drop / 100))
                        qty = round_qty(BASE_QTY * GRID_MULTIPLIERS[i])
                        
                        session.place_order(
                            category="linear", symbol=SYMBOL, side="Buy",
                            orderType="Limit", qty=str(qty), price=str(entry_price)
                        )

                    last_trade_time = now
                    last_tp_price = 0.0
                    last_tp_update_time = 0
                    print(f"📍 {len(GRID_MULTIPLIERS)-1} ordini grid piazzati")

        time.sleep(5)

    except Exception as e:
        print(f"⚠️ Errore: {e}")
        time.sleep(10)
