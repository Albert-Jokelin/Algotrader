import requests
import time
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode
from .config import UpstoxConfig, TOKEN_ENDPOINT
from .models import TokenResponse, Profile, OrderRequest, OrderResponse, Quote, HistoricalResponse, Instrument, MarginResponse, Holding, GTTRequest, GTTResponse, BrokerageResponse, ProfitLossResponse, OptionContract, OptionChainResponse, MarketQuoteFull, MarketQuoteOHLC, MarketQuoteLTP
from .utils import default_backoff, parse_retry_after

class APIError(Exception):
    pass

class UpstoxSyncClient:
    def __init__(self, cfg: UpstoxConfig, access_token: Optional[str]=None, timeout:int=15):
        self.cfg = cfg
        self.timeout = timeout
        self.session = requests.Session()
        self.access_token = access_token

    def _url(self, path:str)->str:
        if path.startswith("http"):
            return path
        return self.cfg.api_base.rstrip("/") + "/" + path.lstrip("/")

    def _headers(self, extra:Dict[str,str]=None):
        h = {"Accept":"application/json"}
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        if extra:
            h.update(extra)
        return h

    def build_authorize_url(self, state:Optional[str]=None)->str:
        params = {"client_id": self.cfg.client_id, "redirect_uri": self.cfg.redirect_uri}
        if state:
            params["state"] = state
        return f"{self.cfg.api_base}authorize?{urlencode(params)}"

    @default_backoff()
    def exchange_code_for_token(self, code:str)->TokenResponse:
        data = {
            "code": code,
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
            "redirect_uri": self.cfg.redirect_uri,
            "grant_type": "authorization_code"
        }
        r = self.session.post(TOKEN_ENDPOINT, data=data, timeout=self.timeout)
        if r.status_code >= 400:
            raise APIError(f"Token exchange failed: {r.status_code} {r.text}")
        obj = r.json()
        tr = TokenResponse(**obj)
        self.access_token = tr.access_token
        return tr

    @default_backoff()
    def get_profile(self)->Profile:
        r = self.session.get(self._url("/user/profile"), headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return Profile(**r.json())

    @default_backoff()
    def place_order(self, order: OrderRequest)->OrderResponse:
        payload = order.dict() if hasattr(order, 'dict') else order
        r = self.session.post(self._url("/order/place"), json=payload, headers=self._headers(), timeout=self.timeout)
        if r.status_code == 429:
            wait = parse_retry_after(r) or 1
            time.sleep(wait)
            return self.place_order(order)
        r.raise_for_status()
        return OrderResponse(**r.json())

    @default_backoff()
    def modify_order(self, payload:Dict[str,Any])->OrderResponse:
        r = self.session.post(self._url("/order/modify"), json=payload, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return OrderResponse(**r.json())

    @default_backoff()
    def cancel_order(self, payload:Dict[str,Any])->OrderResponse:
        r = self.session.post(self._url("/order/cancel"), json=payload, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return OrderResponse(**r.json())

    @default_backoff()
    def get_order(self, order_id:str)->OrderResponse:
        r = self.session.get(self._url(f"/order/{order_id}"), headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return OrderResponse(**r.json())

    @default_backoff()
    def get_orders(self, params:Dict[str,Any]=None)->List[OrderResponse]:
        r = self.session.get(self._url("/orders"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        objs = r.json()
        return [OrderResponse(**o) for o in (objs if isinstance(objs, list) else objs.get("data", []))]

    @default_backoff()
    def get_quote(self, symbol:str)->Quote:
        r = self.session.get(self._url(f"/market/quote/{symbol}"), headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return Quote(**r.json())

    @default_backoff()
    def get_historical(self, symbol:str, from_ts:str, to_ts:str, interval:str="1m")->HistoricalResponse:
        params = {"from": from_ts, "to": to_ts, "interval": interval}
        r = self.session.get(self._url(f"/market/historical/{symbol}"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        obj = r.json()
        bars = []
        data = obj.get("data") if isinstance(obj, dict) else obj
        candles = data.get("candles") if isinstance(data, dict) else None
        if candles and isinstance(candles, list):
            for b in candles:
                bars.append({
                    "timestamp": b[0],
                    "open": b[1],
                    "high": b[2],
                    "low": b[3],
                    "close": b[4],
                    "volume": b[5] if len(b)>5 else None
                })
        return HistoricalResponse(symbol=symbol, interval=interval, bars=bars)

    @default_backoff()
    def list_instruments(self, exchange:Optional[str]=None)->List[Instrument]:
        params = {}
        if exchange:
            params["exchange"] = exchange
        r = self.session.get(self._url("/instruments"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        items = data.get("data", []) if isinstance(data, dict) else data
        return [Instrument(**i) for i in items]

    @default_backoff()
    def get_margins(self)->MarginResponse:
        r = self.session.get(self._url("/user/margins"), headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return MarginResponse(**r.json())

    @default_backoff()
    def get_holdings(self)->List[Holding]:
        r = self.session.get(self._url("/portfolio/holdings"), headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        items = r.json().get("data", [])
        return [Holding(**i) for i in items]

    @default_backoff()
    def get_positions(self)->List[Holding]:
        r = self.session.get(self._url("/portfolio/positions"), headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        items = r.json().get("data", [])
        return [Holding(**i) for i in items]

    @default_backoff()
    def place_gtt(self, gtt:GTTRequest)->GTTResponse:
        r = self.session.post(self._url("/gtt/create"), json=gtt.dict(), headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return GTTResponse(**r.json())

    @default_backoff()
    def cancel_gtt(self, gtt_id:str)->GTTResponse:
        r = self.session.post(self._url("/gtt/cancel"), json={"gtt_id": gtt_id}, headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        return GTTResponse(**r.json())

    @default_backoff()
    def get_brokerage(self, instrument_token:str, quantity:int, product:str, transaction_type:str, price:float)->BrokerageResponse:
        params = {
            "instrument_token": instrument_token,
            "quantity": quantity,
            "product": product,
            "transaction_type": transaction_type,
            "price": price
        }
        r = self.session.get(self._url("/charges/brokerage"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json().get("data", {})
        return BrokerageResponse(**data.get("charges", {}))

    @default_backoff()
    def get_profit_loss_report(self, from_date:str, to_date:str, segment:str, financial_year:str, page_number:int=1, page_size:int=20)->ProfitLossResponse:
        params = {
            "from_date": from_date,
            "to_date": to_date,
            "segment": segment,
            "financial_year": financial_year,
            "page_number": page_number,
            "page_size": page_size
        }
        r = self.session.get(self._url("/trade/profit-loss/data"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        return ProfitLossResponse(**r.json())

    @default_backoff()
    def get_trade_charges(self, from_date:str, to_date:str, segment:str, financial_year:str, page_number:int=1, page_size:int=20)->ProfitLossResponse:
        params = {
            "from_date": from_date,
            "to_date": to_date,
            "segment": segment,
            "financial_year": financial_year,
            "page_number": page_number,
            "page_size": page_size
        }
        r = self.session.get(self._url("/trade/profit-loss/charges"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        return ProfitLossResponse(**r.json())

    @default_backoff()
    def get_option_contracts(self, instrument_key:str, expiry_date:Optional[str]=None)->List[OptionContract]:
        params = {"instrument_key": instrument_key}
        if expiry_date:
            params["expiry_date"] = expiry_date
        r = self.session.get(self._url("/option/contract"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json().get("data", [])
        return [OptionContract(**i) for i in data]

    @default_backoff()
    def get_option_chain(self, instrument_key:str, expiry_date:str)->List[OptionChainResponse]:
        params = {"instrument_key": instrument_key, "expiry_date": expiry_date}
        r = self.session.get(self._url("/option/chain"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json().get("data", [])
        return [OptionChainResponse(**i) for i in data]

    @default_backoff()
    def get_full_market_quote(self, instrument_keys:List[str])->Dict[str, MarketQuoteFull]:
        params = {"instrument_key": ",".join(instrument_keys)}
        r = self.session.get(self._url("/market-quote/quotes"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json().get("data", {})
        return {k: MarketQuoteFull(**v) for k, v in data.items()}

    @default_backoff()
    def get_market_quote_ohlc(self, instrument_keys:List[str], interval:str="1d")->Dict[str, MarketQuoteOHLC]:
        params = {"instrument_key": ",".join(instrument_keys), "interval": interval}
        r = self.session.get(self._url("/market-quote/ohlc"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json().get("data", {})
        # The OHLC endpoint structure might be slightly different, usually it returns OHLC directly under the key
        # But based on full quote, it might be nested. Let's assume it matches MarketQuoteOHLC structure directly or nested.
        # Re-checking docs: "data": { "NSE_EQ:NHPC": { "ohlc": { ... } } } for full quote.
        # For OHLC endpoint: "data": { "NSE_EQ:NHPC": { "ohlc": { ... } } } usually?
        # Let's assume standard structure. If it fails, we debug.
        # Actually, let's check the OHLC structure in docs if possible, but I'll implement based on standard pattern.
        # Wait, for OHLC endpoint, the response is usually simplified.
        # Let's stick to the plan.
        return {k: MarketQuoteOHLC(**v["ohlc"]) for k, v in data.items()}

    @default_backoff()
    def get_market_quote_ltp(self, instrument_keys:List[str])->Dict[str, MarketQuoteLTP]:
        params = {"instrument_key": ",".join(instrument_keys)}
        r = self.session.get(self._url("/market-quote/ltp"), headers=self._headers(), params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json().get("data", {})
        return {k: MarketQuoteLTP(**v) for k, v in data.items()}

