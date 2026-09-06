import logging
import threading

import ensemble
from models import db, StrategyPerformance, TradeLog
from utils import setup_api, retry_api_call, get_bars_df

logging.basicConfig(
    filename="trading_bot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

DEFAULT_WATCHLIST = ["AAPL", "MSFT", "GOOG", "AMZN", "NVDA"]


def get_stock_list(api):
    """Explicit watchlist, filtered to symbols Alpaca currently allows trading.
    Deliberately small and readable rather than scanning the whole market —
    widen this once the strategy set has a track record you trust.
    """
    try:
        assets = retry_api_call(api.list_assets, status="active")
        tradable = {a.symbol for a in assets if a.tradable}
        return [s for s in DEFAULT_WATCHLIST if s in tradable]
    except Exception as e:
        logging.error(f"Error fetching stock list: {e}")
        return DEFAULT_WATCHLIST


def get_or_create_performance(user_id, strategy_name):
    row = StrategyPerformance.query.filter_by(user_id=user_id, strategy_name=strategy_name).first()
    if row is None:
        row = StrategyPerformance(user_id=user_id, strategy_name=strategy_name)
        db.session.add(row)
        db.session.commit()
    return row


def buy_stock(symbol, dollar_amount, api):
    if dollar_amount <= 0:
        return False
    try:
        retry_api_call(
            api.submit_order,
            symbol=symbol,
            notional=round(dollar_amount, 2),
            side="buy",
            type="market",
            time_in_force="day",
        )
        logging.info(f"Buy order submitted: {symbol} for ${dollar_amount:.2f}")
        return True
    except Exception as e:
        logging.error(f"Failed to buy {symbol}: {e}")
        return False


def sell_stock(symbol, qty, api):
    if not qty or float(qty) <= 0:
        return
    try:
        retry_api_call(
            api.submit_order,
            symbol=symbol,
            qty=qty,
            side="sell",
            type="market",
            time_in_force="day",
        )
        logging.info(f"Sell order submitted: {qty} shares of {symbol}")
    except Exception as e:
        logging.error(f"Failed to sell {symbol}: {e}")


def monitor_position(user_id, symbol, strategy_names, buy_price, api, profit_target, stop_loss, stop_event, poll_seconds=60):
    while not stop_event.is_set():
        try:
            position = retry_api_call(api.get_position, symbol)
            qty = position.qty
            current_price = retry_api_call(api.get_last_trade, symbol).price

            hit_target = current_price >= buy_price * (1 + profit_target)
            hit_stop = current_price <= buy_price * (1 - stop_loss)

            if hit_target or hit_stop:
                sell_stock(symbol, qty, api)
                pnl = (current_price - buy_price) * float(qty)
                reason = "profit target" if hit_target else "stop-loss"
                logging.info(f"{symbol}: {reason} hit, pnl=${pnl:.2f}")

                trade = TradeLog(
                    user_id=user_id, symbol=symbol,
                    strategy_name=",".join(strategy_names),
                    side="sell", qty=float(qty), price=current_price, pnl=pnl,
                )
                db.session.add(trade)
                # Attribute this outcome to every contributing strategy so
                # the ensemble can learn which ones actually called it right.
                for name in strategy_names:
                    perf = get_or_create_performance(user_id, name)
                    perf.record_trade(pnl)
                db.session.commit()
                return
        except Exception as e:
            logging.error(f"Error monitoring {symbol}: {e}")
            return

        stop_event.wait(poll_seconds)

    logging.info(f"Stop requested; leaving {symbol} position open for manual review.")


def run_cycle(app, user_id, api_key, api_secret, base_url, money_available, stop_event,
              profit_target=0.10, stop_loss=0.05, cycle_seconds=15 * 60):
    """Main loop for one user's account. Requires `app` (the Flask app) to
    push an application context, since this runs in a background thread
    that needs its own database session.
    """
    with app.app_context():
        api = setup_api(api_key, api_secret, base_url)
        watchlist = get_stock_list(api)

        if not watchlist:
            logging.error(f"user {user_id}: no tradable symbols; stopping.")
            return

        performance_by_strategy = {
            name: get_or_create_performance(user_id, name)
            for name in ["ma_crossover", "rsi_reversion", "macd_momentum",
                         "bollinger_reversion", "zscore_mean_reversion",
                         "donchian_breakout", "vwap_trend"]
        }

        while not stop_event.is_set():
            # Pass 1: evaluate every symbol first so capital can be split by
            # conviction (see ensemble.allocate_capital) instead of an equal
            # share decided before we even know which symbols the ensemble
            # actually likes this cycle.
            decisions = {}
            prices = {}
            for symbol in watchlist:
                if stop_event.is_set():
                    break
                try:
                    df = get_bars_df(symbol, api, limit=220)
                    closes = df["c"].tolist() if not df.empty else []
                    if len(closes) < 20:
                        continue

                    decision = ensemble.decide(symbol, closes, df, performance_by_strategy)
                    if decision["direction"] == 1 and decision["score"] > 0.25:
                        decisions[symbol] = decision
                        prices[symbol] = closes[-1]
                except Exception as e:
                    logging.error(f"user {user_id}: error evaluating {symbol}: {e}")

            # Pass 2: allocate and execute.
            symbol_scores = {s: d["score"] for s, d in decisions.items()}
            allocation = ensemble.allocate_capital(money_available, symbol_scores)

            for symbol, decision in decisions.items():
                if stop_event.is_set():
                    break
                try:
                    current_price = prices[symbol]
                    if buy_stock(symbol, allocation.get(symbol, 0), api):
                        trade = TradeLog(
                            user_id=user_id, symbol=symbol,
                            strategy_name=",".join(decision["contributing_strategies"]),
                            side="buy", notional=allocation.get(symbol, 0), price=current_price,
                        )
                        db.session.add(trade)
                        db.session.commit()
                        monitor_position(
                            user_id, symbol, decision["contributing_strategies"],
                            current_price, api, profit_target, stop_loss, stop_event,
                        )
                except Exception as e:
                    logging.error(f"user {user_id}: error trading {symbol}: {e}")

            stop_event.wait(cycle_seconds)

        logging.info(f"user {user_id}: trading loop stopped cleanly.")
