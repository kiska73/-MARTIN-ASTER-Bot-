import os
import time
import pandas as pd
from pybit.unified_trading import HTTP
from datetime import datetime, timezone

# ==========================================================
# CONFIGURAZIONE PRINCIPALE
# ==========================================================
SYMBOL = "HYPEUSDT"
BASE_QTY = 0.20
PERC_PAUSE = 1.0

GRID_MULTIPLIERS = [1, 1, 1, 2, 2, 3, 4, 5, 6, 7, 9, 11, 13]

current_mode = "AGGRESSIVE"
COOLDOWN = 20

# ==========================================================
# DECIMALI (HYPEUSDT → Price: 3, Qty: 2)
# ==========================================================
PRICE_DECIMALS = 3      
QTY_DECIMALS = 2        

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
# FUNZIONI OPERATIVE
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
        # Prende 41 candele per estrarre esattamente 40 candele chiuse stabili
        data = session.get_kline(category="linear", symbol=symbol, interval="240", limit=41)
        df = pd.DataFrame(data['result']['list'], 
                         columns=['ts', 'open', 'high', 'low', 'close', 'vol', 'turnover'])
        
        df['close'] = df['close'].astype(float)
        df['low'] = df['low'].astype(float)
        df['ts'] = df['ts'].astype(int)
        
        # ESCLUDI L'ULTIMA RIGA: analizziamo solo l'ora reale di chiusura passata
        df_closed = df.iloc[:-1].copy()
        
        sma = df_closed['close'].rolling(window=40).mean()
        std = df_closed['close'].rolling(window=40).std()
        lower_band = sma - (std * 2)
        
        bb_width_percent = ((sma.iloc[-1] - lower_band.iloc[-1]) / sma.iloc[-1]) * 100
        
        return {
            'ts': df_closed['ts'].iloc[-1],
            'bb_width': round(bb_width_percent, 2),
            'lower_band': round(lower_band.iloc[-1], 4),
            'low': round(df_closed['low'].iloc[-1], 4)
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
    # Esegue il check tra il secondo 20 e 40 per dare tempo a Bybit di consolidare la candela chiusa
    return (now_utc.hour % 4 == 0 and now_utc.minute == 0 and 20 <= now_utc.second <= 40)


# ==========================================================
# AVVIO BOT IN PRODUZIONE
# ==========================================================
print("🚀 BOT MASTER LIVE - Griglia Automatizzata v3.0")
print(f"Symbol: {SYMBOL} | BASE_QTY: {BASE_QTY} | Price Decimals: {PRICE_DECIMALS}\n")

while True:
    try:
        now = time.time()
        price = get_current_price()
        if not price:
            time.sleep(2)
            continue

        pos_data = session.get_positions(category="linear", symbol=SYMBOL)["result"]["list"][0]
        size = float(pos_data["size"])
        avg_price = float(pos_data.get("avgPrice", 0))

        active_orders = session.get_open_orders(category="linear", symbol=SYMBOL)["result"]["list"]

        # ==================== CONTROLLO IMPERATIVO CANDELA 4H ====================
        if should_check_candle():
            vol_data = get_volatility_data(SYMBOL)
            if vol_data and vol_data['ts'] > last_candle_ts:
                print(f"📊 Analisi Candela Chiusa 4H → BB Width: {vol_data['bb_width']}%")

                # Controllo di Emergenza sulla candela chiusa
                if price < vol_data['lower_band']:
                    print("🚨 CRITICO: Chiusura sotto la Lower Band rilevata!")
                    cancel_all_orders()
                    close_position()
                    pause_until_next_candle = True
                    last_trade_time = now + 60
                else:
                    # Se il mercato ha recuperato ed è sopra la banda, rimuove la pausa automatica
                    if pause_until_next_candle:
                        print("▶️ Il prezzo è tornato in zona sicura. Sblocco la pausa.")
                        pause_until_next_candle = False

                new_mode = "CONSERVATIVE" if vol_data.get('bb_width', 0) > 40 else "AGGRESSIVE"
                if new_mode != current_mode:
                    print(f"🔄 CAMBIO MODALITÀ → {new_mode}")
                    current_mode = new_mode

                last_candle_ts = vol_data['ts']

        # ==================== GESTIONE TARGET TAKE PROFIT ====================
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

        # ==================== APERTURA NUOVA ENTRATA + GRIGLIA ====================
        elif size == 0 and (now - last_trade_time > COOLDOWN):
            if pause_until_next_candle:
                print(f"⏸️ SISTEMA IN PAUSA DI PROTEZIONE | Prezzo: {price:.4f}")
                cancel_all_orders()
            else:
                vol_data = get_volatility_data(SYMBOL)
                if vol_data:
                    # Lo Stop Loss dinamico viene calcolato sul minimo (low) della candela chiusa precedente
                    sl_price = round_price(vol_data['low'])
                    
                    print(f"🟢 NUOVA ENTRATA @ {price:.4f} | Mode: {current_mode} | SL Server: {sl_price}")
                    cancel_all_orders()
                    time.sleep(1.5)

                    # Apertura ordine di mercato INIETTANDO lo Stop Loss direttamente sui server di Bybit
                    session.place_order(
                        category="linear", symbol=SYMBOL, side="Buy", 
                        orderType="Market", qty=str(BASE_QTY),
                        stopLoss=str(sl_price), slTriggerBy="LastPrice"
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
                        print(f"📍 {len(GRID_MULTIPLIERS)-1} ordini grid piazati correttamente")

        time.sleep(5)

    except Exception as e:
        print(f"⚠️ Errore critico nel Loop principale: {e}")
        time.sleep(10)
