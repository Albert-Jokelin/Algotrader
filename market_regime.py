'''
# Market Regime
The market regime is a dynamic variable that measures the market's current state. It is often used to determine the market's direction and direction of change.

This code aims to predict the market regimes as described by Jim Simmons.
'''

import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt

# Step 1: Fetch historical price data
ticker = "^NSEI"
data = yf.download(ticker, start="2020-01-01", end="2024-11-22")
print(data.head())

data['20_SMA'] = data['Close'].rolling(window=20).mean()  # Simple Moving Average (SMA)
data['50_SMA'] = data['Close'].rolling(window=50).mean()
data['ATR'] = (data['High'] - data['Low']).rolling(window=14).mean()  # Average True Range (ATR)

# Step 2: Calculate ADX (trend strength indicator)
def calculate_adx(data, window=14):
    df = data.copy()
    df['+DM'] = np.where((df['High'] - df['High'].shift(1)) > (df['Low'].shift(1) - df['Low']),
                         df['High'] - df['High'].shift(1), 0)
    df['-DM'] = np.where((df['Low'].shift(1) - df['Low']) > (df['High'] - df['High'].shift(1)),
                         df['Low'].shift(1) - df['Low'], 0)
    df['TR'] = np.max([df['High'] - df['Low'],
                       abs(df['High'] - df['Close'].shift(1)),
                       abs(df['Low'] - df['Close'].shift(1))], axis=0)
    df['+DI'] = 100 * (df['+DM'] / df['TR']).rolling(window=window).mean()
    df['-DI'] = 100 * (df['-DM'] / df['TR']).rolling(window=window).mean()
    df['DX'] = abs(df['+DI'] - df['-DI']) / (df['+DI'] + df['-DI']) * 100
    df['ADX'] = df['DX'].rolling(window=window).mean()
    return df['ADX']

data['ADX'] = calculate_adx(data)
print("Number of elements in data:", data.size)
print("Number of NaNs in data:", data.isna().sum())
data.dropna(axis=0, inplace=True)
print("Number of NaNs in data:", data.isna().sum())
print(data[['ADX', '20_SMA', '50_SMA']].dtypes)

# Step 3: Define market regimes
def classify_regime(row):
    # print(row)
    if int(row['ADX']) > 25:
      if int(row['20_SMA']) > int(row['50_SMA']):
        return "Trending"
    elif int(row['ATR']) > int(data['ATR'].mean()) * 1.5:
        return "Volatile"
    else:
        return "Ranging"

data['Regime'] = data.apply(classify_regime, axis=1)
data.dropna(inplace=True)
# Step 4: Plot the data with regimes
csv_file = data
print(data)
plt.figure(figsize=(14, 7))
for regime in data['Regime'].unique():
    subset = data[data['Regime'] == regime]
    if subset.empty:
        print(f"Warning: No data for regime '{regime}'")
    else:
        print(f"Plotting data for regime '{regime}'")
    plt.plot(data[data['Regime'] == regime].index,
             data[data['Regime'] == regime]['Close'], label=regime)
plt.legend()
plt.title(f"{ticker} Market Regimes")
plt.xlabel("Date")
plt.ylabel("Close Price")
plt.grid()
plt.show()

# Save the data with regimes
csv_file.to_csv("market_regimes.csv")

