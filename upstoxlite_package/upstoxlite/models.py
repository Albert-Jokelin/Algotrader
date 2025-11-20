from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class TokenResponse(BaseModel):
    access_token: str
    token_type: Optional[str]
    expires_in: Optional[int]
    refresh_token: Optional[str]
    scope: Optional[str]
    user: Optional[Dict[str, Any]]

class Profile(BaseModel):
    user_id: Optional[str]
    name: Optional[str]
    email: Optional[str]
    mobile: Optional[str]
    client_id: Optional[str]

class OrderRequest(BaseModel):
    exchange: str
    symbol: str
    transaction_type: str
    quantity: int
    order_type: Optional[str] = "MARKET"
    price: Optional[float] = None
    product: Optional[str] = None
    trigger_price: Optional[float] = None
    validity: Optional[str] = None

class OrderResponse(BaseModel):
    order_id: Optional[str]
    status: Optional[str]
    data: Optional[Dict[str, Any]]

class Quote(BaseModel):
    symbol: str
    last_price: Optional[float]
    timestamp: Optional[int]
    raw: Optional[Dict[str, Any]]

class HistoricalBar(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float]

class HistoricalResponse(BaseModel):
    symbol: str
    interval: str
    bars: List[HistoricalBar]

class Instrument(BaseModel):
    exchange: str
    symbol: str
    instrument_token: Optional[str]
    lot_size: Optional[int]
    expiry: Optional[str]
    strike: Optional[float]
    option_type: Optional[str]

class MarginResponse(BaseModel):
    available_cash: Optional[float]
    used_margin: Optional[float]
    span: Optional[float]
    raw: Optional[Dict[str, Any]]

class Holding(BaseModel):
    symbol: str
    quantity: float
    avg_price: float
    pnl: Optional[float]
    raw: Optional[Dict[str, Any]]

class GTTRequest(BaseModel):
    trigger_price: float
    order: OrderRequest
    expiry: Optional[str]

class GTTResponse(BaseModel):
    gtt_id: Optional[str]
    status: Optional[str]
    data: Optional[Dict[str, Any]]

class BrokerageTaxes(BaseModel):
    gst: float
    stt: float
    stamp_duty: float

class BrokerageOtherCharges(BaseModel):
    transaction: float
    clearing: float
    ipft: float
    sebi_turnover: float

class BrokerageResponse(BaseModel):
    total: float
    brokerage: float
    taxes: BrokerageTaxes
    other_charges: BrokerageOtherCharges
    dp_plan: Optional[Dict[str, Any]]

class ProfitLossMetadata(BaseModel):
    page_number: int
    page_size: int

class TradeData(BaseModel):
    quantity: float
    isin: str
    scrip_name: str
    trade_type: str
    buy_date: str
    buy_average: float
    sell_date: str
    sell_average: float
    buy_amount: float
    sell_amount: float

class ProfitLossResponse(BaseModel):
    data: List[TradeData]
    metadata: Optional[Dict[str, Any]]

class OptionContract(BaseModel):
    name: str
    segment: str
    exchange: str
    expiry: str
    instrument_key: str
    exchange_token: Optional[str]
    trading_symbol: str
    tick_size: float
    lot_size: float
    instrument_type: Optional[str]
    strike_price: float
    underlying_key: str
    underlying_type: str
    underlying_symbol: str
    weekly: bool

class OptionGreek(BaseModel):
    vega: Optional[float]
    theta: Optional[float]
    gamma: Optional[float]
    delta: Optional[float]
    iv: Optional[float]
    pop: Optional[float]

class OptionMarketData(BaseModel):
    ltp: float
    volume: float
    oi: float
    close_price: float
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    prev_oi: float

class OptionChainItem(BaseModel):
    instrument_key: str
    market_data: OptionMarketData
    option_greeks: OptionGreek

class OptionChainResponse(BaseModel):
    expiry: str
    pcr: Optional[float]
    strike_price: float
    underlying_key: str
    underlying_spot_price: float
    call_options: Optional[OptionChainItem]
    put_options: Optional[OptionChainItem]

class MarketQuoteOHLC(BaseModel):
    open: float
    high: float
    low: float
    close: float

class MarketQuoteDepth(BaseModel):
    buy: List[Dict[str, Any]]
    sell: List[Dict[str, Any]]

class MarketQuoteFull(BaseModel):
    ohlc: MarketQuoteOHLC
    depth: Optional[MarketQuoteDepth]
    timestamp: Optional[str]
    instrument_token: str
    symbol: str
    last_price: float
    volume: float
    average_price: float
    oi: float
    net_change: float
    total_buy_quantity: float
    total_sell_quantity: float
    lower_circuit_limit: float
    upper_circuit_limit: float
    last_trade_time: Optional[str]
    oi_day_high: Optional[float]
    oi_day_low: Optional[float]

class MarketQuoteLTP(BaseModel):
    instrument_token: str
    last_price: float

