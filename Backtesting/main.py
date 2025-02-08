from upstox_api.api import Upstox, OHLCInterval
from datetime import datetime, timedelta
import pandas as pd

# Configuration
API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
REDIRECT_URI = "your_redirect_uri"  # Set this in the Upstox developer console
ACCESS_TOKEN = "your_access_token"  # Generate once and store securely

class UpstoxTradingBot:
    def __init__(self, api_key, api_secret, access_token):
        self.upstox = Upstox(api_key, access_token)
        self.upstox.set_session(api_key, api_secret)
        print("Logged in successfully!")

    def fetch_historical_data(self, symbol, exchange, interval, days=7):
        """
        Fetch historical OHLC data for a symbol.
        """
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)
        ohlc_data = self.upstox.get_ohlc(
            self.upstox.get_instrument_by_symbol(exchange, symbol),
            OHLCInterval(interval),
            from_date,
            to_date,
        )
        return pd.DataFrame(ohlc_data)

    def get_indicator(self, ohlc_data, indicator_name):
        """
        Compute a specific technical indicator.
        """
        if indicator_name == "SMA":
            return ohlc_data['close'].rolling(window=14).mean()
        elif indicator_name == "EMA":
            return ohlc_data['close'].ewm(span=14, adjust=False).mean()
        elif indicator_name == "RSI":
            return self.calculate_rsi(ohlc_data['close'])
        else:
            raise ValueError("Unsupported indicator!")

    def calculate_rsi(self, series, period=14):
        """
        Calculate Relative Strength Index (RSI).
        """
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def place_order(self, symbol, exchange, transaction_type, quantity, order_type="MARKET"):
        """
        Place a buy or sell order.
        """
        instrument = self.upstox.get_instrument_by_symbol(exchange, symbol)
        self.upstox.place_order(
            transaction_type=transaction_type,
            instrument=instrument,
            quantity=quantity,
            order_type=order_type,
            product="CNC",
            duration="DAY"
        )
        print(f"Order placed: {transaction_type} {quantity} of {symbol}")

    def execute_strategy(self, strategy_func, *args, **kwargs):
        """
        Execute a custom strategy.
        """
        strategy_func(self, *args, **kwargs)

# Example Strategy
def sma_crossover_strategy(bot, symbol, exchange):
    """
    Simple Moving Average (SMA) crossover strategy.
    """
    ohlc_data = bot.fetch_historical_data(symbol, exchange, "1D")
    sma_short = bot.get_indicator(ohlc_data, "SMA")
    sma_long = bot.get_indicator(ohlc_data.rolling(window=50), "SMA")

    if sma_short.iloc[-1] > sma_long.iloc[-1]:  # Buy signal
        bot.place_order(symbol, exchange, "BUY", quantity=1)
    elif sma_short.iloc[-1] < sma_long.iloc[-1]:  # Sell signal
        bot.place_order(symbol, exchange, "SELL", quantity=1)

if __name__ == "__main__":
    # Initialize the bot
    bot = UpstoxTradingBot(API_KEY, API_SECRET, ACCESS_TOKEN)

    # Execute a strategy
    bot.execute_strategy(sma_crossover_strategy, symbol="RELIANCE", exchange="NSE")
