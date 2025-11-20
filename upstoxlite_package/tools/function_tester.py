#!/usr/bin/env python3
"""
Interactive function tester for upstoxlite.
Uses UpstoxConfig + UpstoxSyncClient to exercise endpoints.
Saves token to ~/.upstox_token.json for convenience.
"""
import json
from pathlib import Path
from getpass import getpass

from upstoxlite import UpstoxConfig
from upstoxlite.clients import UpstoxSyncClient

TOKEN_FILE = Path.home() / ".upstox_token.json"

def prompt_creds():
    cid = input("Client ID: ").strip()
    secret = getpass("Client Secret: ").strip()
    redirect = input("Redirect URI (https://localhost:3000/callback): ").strip() or "https://localhost:3000/callback"
    sandbox = input("Sandbox? (Y/n): ").strip().lower() not in ('n','no')
    return cid, secret, redirect, sandbox

def save_token(cfg, token):
    TOKEN_FILE.write_text(json.dumps({"client_id": cfg.client_id, "sandbox": cfg.sandbox, "token": token}))
    print("Saved token to", TOKEN_FILE)

def load_token(cfg):
    if not TOKEN_FILE.exists():
        return None
    data = json.loads(TOKEN_FILE.read_text())
    if data.get("client_id") != cfg.client_id or data.get("sandbox") != cfg.sandbox:
        return None
    return data.get("token")

def build_sample_order():
    return {
        "exchange": "NSE",
        "symbol": "RELIANCE",
        "transaction_type": "BUY",
        "quantity": 1,
        "order_type": "MARKET",
        "product": "MIS"
    }

def interactive(cfg):
    client = UpstoxSyncClient(cfg)
    menu = [
        "Build auth URL",
        "Exchange code for token",
        "Get profile",
        "Get quote",
        "Place order (sample)",
        "List instruments",
        "Get holdings",
        "Start websocket (manual)",
        "Exit"
    ]
    while True:
        print("\nOptions:")
        for i, m in enumerate(menu, start=1):
            print(f"{i}) {m}")
        choice = input("Choice: ").strip()
        if choice == "1":
            print("Authorize URL:\n", client.build_authorize_url())
        elif choice == "2":
            code = input("Paste authorization code: ").strip()
            try:
                tr = client.exchange_code_for_token(code)
                print("Token response:", getattr(tr, "dict", lambda: tr)())
                save = input("Save token? (Y/n): ").strip().lower() not in ('n','no')
                if save:
                    save_token(cfg, tr.dict() if hasattr(tr, "dict") else tr)
            except Exception as e:
                print("Token exchange failed:", e)
        elif choice == "3":
            token = load_token(cfg)
            if token:
                client.access_token = token.get('access_token') if isinstance(token, dict) else token
            try:
                profile = client.get_profile()
                print("Profile:", getattr(profile, "dict", lambda: profile)())
            except Exception as e:
                print("Get profile failed:", e)
        elif choice == "4":
            sym = input("Symbol (e.g., NSE:RELIANCE): ").strip()
            try:
                q = client.get_quote(sym)
                print("Quote:", getattr(q, "dict", lambda: q)())
            except Exception as e:
                print("Get quote failed:", e)
        elif choice == "5":
            sample = build_sample_order()
            print("Sample order:\n", json.dumps(sample, indent=2))
            use = input("Use sample? (Y/n): ").strip().lower() not in ('n','no')
            payload = sample if use else json.loads(input("Paste JSON payload: "))
            try:
                resp = client.place_order(payload if isinstance(payload, dict) else payload)
                print("Order response:", getattr(resp, "dict", lambda: resp)())
            except Exception as e:
                print("Place order failed:", e)
        elif choice == "6":
            ex = input("Exchange (leave blank for all): ").strip() or None
            try:
                items = client.list_instruments(ex)
                print(f"Found {len(items)} instruments (showing up to 10):")
                for it in items[:10]:
                    print(getattr(it, "dict", lambda: it)())
            except Exception as e:
                print("List instruments failed:", e)
        elif choice == "7":
            try:
                holdings = client.get_holdings()
                print("Holdings:")
                for h in holdings:
                    print(getattr(h, "dict", lambda: h)())
            except Exception as e:
                print("Get holdings failed:", e)
        elif choice == "8":
            print("Websocket start is manual here. Use the ws client module with an access token (see README).")
        elif choice == "9":
            print("Exiting.")
            break
        else:
            print("Unknown choice.")

def main():
    cid, secret, redirect, sandbox = prompt_creds()
    cfg = UpstoxConfig(client_id=cid, client_secret=secret, redirect_uri=redirect, sandbox=sandbox)
    interactive(cfg)

if __name__ == "__main__":
    main()
