import os
import sys
from dotenv import load_dotenv
from upstoxlite.clients import UpstoxSyncClient
from upstoxlite.config import UpstoxConfig
from upstoxlite.models import OrderRequest

# Load environment variables from .env file
load_dotenv()

def get_input(prompt, default=None):
    if default:
        user_input = input(f"{prompt} [{default}]: ").strip()
        return user_input if user_input else default
    return input(f"{prompt}: ").strip()

def print_menu():
    print("\n--- Upstox API Tester ---")
    print("1. Get Profile")
    print("2. Get Funds/Margins")
    print("3. Get Holdings")
    print("4. Place Order (Buy/Sell)")
    print("5. Get Brokerage")
    print("6. Get Profit & Loss")
    print("7. Get Option Contracts")
    print("8. Get Option Chain")
    print("9. Get Full Market Quote")
    print("10. Exit")

def main():
    api_key = os.getenv("UPSTOX_API_KEY")
    api_secret = os.getenv("UPSTOX_API_SECRET")
    redirect_uri = os.getenv("UPSTOX_REDIRECT_URI")
    access_token = os.getenv("UPSTOX_ACCESS_TOKEN")

    if not api_key or not api_secret or not redirect_uri:
        print("Error: Please set UPSTOX_API_KEY, UPSTOX_API_SECRET, and UPSTOX_REDIRECT_URI in .env file")
        return

    print("\n--- Mode Selection ---")
    print("1. Sandbox (Default)")
    print("2. Production")
    mode_choice = input("Select mode [1]: ").strip()
    is_sandbox = True
    if mode_choice == "2":
        is_sandbox = False

    if is_sandbox:
        print("\n--- Sandbox Mode ---")
        access_token = os.getenv("UPSTOX_SANDBOX_ACCESS_TOKEN")
        if not access_token:
            print("Please generate a Sandbox Access Token from the Upstox Developer Dashboard.")
            access_token = input("Enter Sandbox Access Token: ").strip()
        
        cfg = UpstoxConfig(
            client_id="sandbox-client", # Dummy for sandbox
            client_secret="sandbox-secret", # Dummy for sandbox
            redirect_uri="http://localhost", # Dummy for sandbox
            sandbox=True
        )
        client = UpstoxSyncClient(cfg, access_token=access_token)
        print("Using Sandbox Access Token.")

    else:
        print("\n--- Production Mode ---")
        if not api_key or not api_secret or not redirect_uri:
            print("Error: Please set UPSTOX_API_KEY, UPSTOX_API_SECRET, and UPSTOX_REDIRECT_URI in .env file for Production mode.")
            return

        cfg = UpstoxConfig(
            client_id=api_key,
            client_secret=api_secret,
            redirect_uri=redirect_uri,
            sandbox=False
        )

        client = UpstoxSyncClient(cfg, access_token=access_token)

        if not access_token:
            print("No access token provided. Starting auth flow...")
            auth_url = client.build_authorize_url()
            print(f"Please visit this URL to authorize: {auth_url}")
            code = input("Enter the code from the redirect URL: ").strip()
            try:
                token_resp = client.exchange_code_for_token(code)
                print(f"Access Token: {token_resp.access_token}")
                print("Please save this token in your .env file as UPSTOX_ACCESS_TOKEN for future use.")
            except Exception as e:
                print(f"Auth failed: {e}")
                return
        else:
            print("Using provided access token.")

    while True:
        print_menu()
        choice = input("Enter your choice: ").strip()

        if choice == "1":
            try:
                print(client.get_profile())
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "2":
            try:
                print(client.get_margins())
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "3":
            try:
                holdings = client.get_holdings()
                print(f"Found {len(holdings)} holdings")
                for h in holdings:
                    print(h)
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "4":
            print("\n--- Place Order ---")
            exchange = get_input("Exchange", "NSE_EQ")
            symbol = get_input("Symbol (e.g. RELIANCE)")
            trans_type = get_input("Transaction Type (BUY/SELL)", "BUY")
            qty = int(get_input("Quantity", "1"))
            order_type = get_input("Order Type (MARKET/LIMIT)", "MARKET")
            product = get_input("Product (D/I)", "D")
            price = None
            if order_type.upper() == "LIMIT":
                price = float(get_input("Price"))
            
            try:
                order = OrderRequest(
                    exchange=exchange,
                    symbol=symbol,
                    transaction_type=trans_type,
                    quantity=qty,
                    order_type=order_type,
                    product=product,
                    price=price
                )
                resp = client.place_order(order)
                print(f"Order Placed: {resp}")
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "5":
            print("\n--- Get Brokerage ---")
            inst_token = get_input("Instrument Token (e.g. NSE_EQ|INE848E01016)")
            qty = int(get_input("Quantity", "10"))
            product = get_input("Product (D/I)", "D")
            trans_type = get_input("Transaction Type (BUY/SELL)", "BUY")
            price = float(get_input("Price", "100.0"))
            
            try:
                resp = client.get_brokerage(inst_token, qty, product, trans_type, price)
                print(resp)
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "6":
            print("\n--- Get Profit & Loss ---")
            from_d = get_input("From Date (dd-mm-yyyy)", "01-04-2023")
            to_d = get_input("To Date (dd-mm-yyyy)", "31-03-2024")
            segment = get_input("Segment (EQ/FO)", "EQ")
            fin_year = get_input("Financial Year (e.g. 2324)", "2324")
            
            try:
                resp = client.get_profit_loss_report(from_d, to_d, segment, fin_year)
                print(f"Found {len(resp.data)} records")
                for r in resp.data:
                    print(r)
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "7":
            print("\n--- Get Option Contracts ---")
            inst_key = get_input("Instrument Key (e.g. NSE_INDEX|Nifty 50)", "NSE_INDEX|Nifty 50")
            expiry = get_input("Expiry Date (optional, yyyy-mm-dd)")
            
            try:
                resp = client.get_option_contracts(inst_key, expiry if expiry else None)
                print(f"Found {len(resp)} contracts")
                if resp:
                    print(f"First 5: {resp[:5]}")
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "8":
            print("\n--- Get Option Chain ---")
            inst_key = get_input("Instrument Key (e.g. NSE_INDEX|Nifty 50)", "NSE_INDEX|Nifty 50")
            expiry = get_input("Expiry Date (yyyy-mm-dd)")
            
            try:
                resp = client.get_option_chain(inst_key, expiry)
                print(f"Found {len(resp)} chain items")
                if resp:
                    print(f"First item: {resp[0]}")
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "9":
            print("\n--- Get Full Market Quote ---")
            keys = get_input("Instrument Keys (comma separated)", "NSE_EQ|INE848E01016")
            key_list = [k.strip() for k in keys.split(",")]
            
            try:
                resp = client.get_full_market_quote(key_list)
                for k, v in resp.items():
                    print(f"{k}: {v}")
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "10":
            print("Exiting...")
            break
        
        else:
            print("Invalid choice")

if __name__ == "__main__":
    main()
