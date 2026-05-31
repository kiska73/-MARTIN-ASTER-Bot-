import os
import time
import pandas as pd
from pybit.unified_trading import HTTP
from datetime import datetime, timezone

# ==============================================================================
# CONFIGURAZIONE PRINCIPALE
# ==============================================================================
SYMBOL = "BILLUSDT"
BASE_QTY = 80
PERC_PAUSE = 2.5
GRID_MULTIPLIERS = [1, 1, 1, 2, 2, 3, 4, 5, 6, 7, 9, 11, 13]
current_mode = "AGGRESSIVE"
COOLDOWN = 20

# ==============================================================================
# DECIMALI
# ==============================================================================
PRICE_DECIMALS = 5
QTY_DECIMALS = 0 

# ==============================================================================
# VARIABILI DI STATO
# ==============================================================================
pause_until_next_candle = False
last_candle_ts = 0
last_trade_time = 0
last_tp_price = 0.0
last_tp_update_time = 0
last_sl_price = 0.0   

session = HTTP(testnet=False, 
               api_key=os.environ.get("BYBIT_API_KEY"), 
               api_secret=os.environ.get("BYBIT_API_SECRET"))

# ==============================================================================
# FUNZIONI UTILITARIE
# ==============================================================================
def round_price(price):
    return round(price, PRICE_DECIMALS)

def round_qty(qty):
    if QTY_DECIMALS == 0:
        return int(round(qty))
    return round(qty, QTY_DECIMALS)

def cancel_all_orders():
    try:
        session.cancel_all_orders(category="linear", symbol=SYMBOL)
        time.sleep(1.0)
        print(" Tutti gli ordini cancellati")
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
        print(f" POSIZIONE CHIUSA A MERCATO | Size: {size}")
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
        data = session.get_kline(category="linear", symbol=symbol, interval="240", limit=150)
        df = pd.DataFrame(data['result']['list'][::-1], 
                         columns=['ts', 'open', 'high', 'low', 'close', 'vol', 'turnover'])
        
        df['close'] = df['close'].astype(float)
        df['low']   = df['low'].astype(float)
        df['ts']    = df['ts'].astype(int)
        
        sma = df['close'].rolling(window=40).mean()
        std = df['close'].rolling(window=40).std()
        
        upper_band = sma + (std * 2)
        lower_band = sma - (std * 2)
        
        bb_width_percent = ((upper_band.iloc[-1] - lower_band.iloc[-1]) / sma.iloc[-1]) * 100
        
        return {
            'ts': df['ts'].iloc[-1],
            'bb_width': round(bb_width_percent, 2),
            'lower_band': round(lower_band.iloc[-1], 4),
            'low': round(df['low'].iloc[-2], 4),
            'close': round(df['close'].iloc[-1], 4),
        }
    except Exception as e:
        print(f"Errore Kline BB: {e}")
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

# ==============================================================================
# AVVIO BOT E CICLO PRINCIPALE
# ==============================================================================
print(" BOT MASTER - Griglia + SL Dinamico 4H (v3.3 - Anti Sincro Fix)")
print(f"Symbol: {SYMBOL} | BASE_QTY: {BASE_QTY} | PERC_PAUSE: {PERC_PAUSE}%\n")

while True:
    try:
        now = time.time()
        price = get_current_price()
        
        pos_data = session.get_positions(category="linear", symbol=SYMBOL)["result"]["list"][0]
        size = float(pos_data["size"])
        avg_price = float(pos_data.get("avgPrice", 0))

        active_orders = session.get_open_orders(category="linear", symbol=SYMBOL)["result"]["list"]

        # Se la posizione è zero, puliamo preventivamente le variabili di tracciamento del TP
        if size == 0:
            last_tp_price = 0.0
            last_sl_price = 0.0

        # ==================== CONTROLLO CANDELA 4H / STARTUP TIMING ====================
        if should_check_candle() or last_candle_ts == 0:
            vol_data = get_volatility_data(SYMBOL)
            
            if vol_data and vol_data['ts'] != last_candle_ts:
                print(f" Analisi Mercato → {datetime.now().strftime('%H:%M:%S')} | BB Width (40): {vol_data['bb_width']}% | Low 4H Chiusa: {vol_data['low']}")

                new_mode = "CONSERVATIVE" if vol_data.get('bb_width', 0) > 70 else "AGGRESSIVE"
                if new_mode != current_mode:
                    print(f" CAMBIO MODALITÀ → da {current_mode} a {new_mode}")
                    current_mode = new_mode

                if price and vol_data.get('lower_band'):
                    distance = ((price - vol_data['lower_band']) / vol_data['lower_band']) * 100
                    previous_pause = pause_until_next_candle
                    pause_until_next_candle = (distance <= PERC_PAUSE)
                    
                    print(f"{' [PAUSA ATTIVA]' if pause_until_next_candle else ' [Pausa disattivata]'} | Distanza da Lower Band: {distance:.2f}%")

                    if pause_until_next_candle and not previous_pause:
                        print(" PAUSA ATTIVATA → CHIUSURA FORZATA DELLE POSIZIONI")
                        cancel_all_orders()
                        close_position()
                        last_trade_time = now + 40

                # ==================== SL DINAMICO ====================
                if size > 0:
                    last_low = vol_data['low']
                    lower_band = vol_data['lower_band']
                    
                    if last_low < lower_band:
                        sl_price = round_price(last_low * 0.999)
                        if last_sl_price == 0 or sl_price > last_sl_price:
                            try:
                                session.set_trading_stop(category="linear", symbol=SYMBOL, stopLoss=str(sl_price))
                                last_sl_price = sl_price
                                print(f" SL DINAMICO IMPOSTATO @ {sl_price}")
                            except Exception as e:
                                print(f"Errore set SL: {e}")

                last_candle_ts = vol_data['ts']

        # ==================== GESTIONE TARGET PROFIT (TP) ====================
        if size > 0:
            tp_percent = 1.20 if current_mode == "CONSERVATIVE" else 0.90
            target_tp = round_price(avg_price * (1 + tp_percent / 100))

            if price and price >= target_tp:
                print(f" Prezzo attuale ({price}) >= TP target ({target_tp}). Chiudo a mercato!")
                cancel_all_orders()
                close_position()
                last_trade_time = now  
                last_tp_price = 0.0
                last_tp_update_time = 0
                last_sl_price = 0.0
            
            elif (abs(target_tp - last_tp_price) > 0.00001) and (now - last_tp_update_time > 12):
                tp_orders = [o for o in active_orders if o.get("side") == "Sell" and o.get("orderType") == "Limit" and o.get("reduceOnly") is True]

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
                    try:
                        session.place_order(
                            category="linear", symbol=SYMBOL, side="Sell", orderType="Limit",
                            qty=str(size), price=str(target_tp), reduceOnly=True
                        )
                        last_tp_price = target_tp
                        last_tp_update_time = now
                        print(f" TP impostato → {target_tp} | Prezzo Medio (Avg): {avg_price:.4f}")
                    except Exception as e:
                        print(f" Errore inserimento TP Limit (Possibile sfasamento): {e}. Eseguo reset ed emergenza.")
                        cancel_all_orders()
                        close_position()
                        last_trade_time = now
                        last_tp_price = 0.0
                        last_tp_update_time = 0
                        last_sl_price = 0.0

        # ==================== GESTIONE NUOVA ENTRATA + GRIGLIA ====================
        elif size == 0 and (now - last_trade_time > COOLDOWN):
            safe_price = price if price is not None else 0.0
            
            if pause_until_next_candle:
                print(f" IN PAUSA REGIME | Prezzo attuale: {safe_price:.4f}")
                cancel_all_orders()
            else:
                print(f" AVVIO GRIGLIA @ {safe_price:.4f} | Regime Attivo: {current_mode}")
                cancel_all_orders()
                time.sleep(2.0) # Aumentato leggermente per dare tempo a Bybit di cancellare tutto

                try:
                    session.place_order(category="linear", symbol=SYMBOL, side="Buy", orderType="Market", qty=str(BASE_QTY))
                    print(f" Ordine market iniziale inviato per {BASE_QTY} pezzi.")
                except Exception as e:
                    print(f" Errore critico apertura ordine Market iniziale: {e}")
                    time.sleep(10)
                    continue

                time.sleep(3.0) # Tempo di consolidamento della posizione su Bybit

                new_pos = session.get_positions(category="linear", symbol=SYMBOL)["result"]["list"][0]
                current_size = float(new_pos["size"])
                
                if current_size > 0:
                    avg = float(new_pos["avgPrice"])
                    print(f" Primo ordine eseguito a mercato @ {avg:.4f}. Preparo la griglia...")

                    accumulated_drop = 0
                    success_orders = 0
                    
                    for i in range(1, len(GRID_MULTIPLIERS)):
                        spacing = get_spacing(i, current_mode)
                        accumulated_drop += spacing
                        entry_price = round_price(avg * (1 - accumulated_drop / 100))
                        qty = round_qty(BASE_QTY * GRID_MULTIPLIERS[i])
                        
                        try:
                            session.place_order(
                                category="linear", symbol=SYMBOL, side="Buy",
                                orderType="Limit", qty=str(qty), price=str(entry_price)
                            )
                            success_orders += 1
                        except Exception as grid_err:
                            print(f" [ERRORE GRIGLIA] Livello {i} fallito: {grid_err}")

                    last_trade_time = now
                    last_tp_price = 0.0
                    last_tp_update_time = 0
                    last_sl_price = 0.0
                    print(f" Configurazione completata: {success_orders} ordini limit inseriti correttamente.")

        time.sleep(5)

    except Exception as e:
        print(f" Errore rilevato nel ciclo continuo: {e}")
        time.sleep(10)
