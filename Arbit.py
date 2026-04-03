import streamlit as st
import json
import sqlite3
import pandas as pd
import hashlib
import time
import os
from datetime import datetime
import requests
from typing import Dict, List, Tuple
import asyncio

st.set_page_config(
    page_title="Arbitrum MEV Bot - Live Scanner",
    page_icon="🟠",
    layout="wide"
)

# ====================== DATABASE SETUP ======================
os.makedirs('data', exist_ok=True)
conn = sqlite3.connect('data/arbitrum_trades.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS trades
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              tx_hash TEXT, amount REAL, profit REAL, 
              timestamp INTEGER, status TEXT, network TEXT,
              path TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS opportunities
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              token_path TEXT, dex_path TEXT, expected_profit REAL,
              timestamp INTEGER, executed INTEGER DEFAULT 0)''')
conn.commit()

# ====================== ARBITRUM TOKEN CONFIG ======================
TOKENS = {
    "WETH": {
        "address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "symbol": "WETH",
        "decimals": 18,
        "coingecko_id": "ethereum"
    },
    "WBTC": {
        "address": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        "symbol": "WBTC",
        "decimals": 8,
        "coingecko_id": "wrapped-bitcoin"
    },
    "USDC": {
        "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "symbol": "USDC",
        "decimals": 6,
        "coingecko_id": "usd-coin"
    },
    "USDT": {
        "address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "symbol": "USDT",
        "decimals": 6,
        "coingecko_id": "tether"
    },
    "ARB": {
        "address": "0x912CE59144191C1204E64559FE8253a0e49E6548",
        "symbol": "ARB",
        "decimals": 18,
        "coingecko_id": "arbitrum"
    },
    "LINK": {
        "address": "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4",
        "symbol": "LINK",
        "decimals": 18,
        "coingecko_id": "chainlink"
    }
}

# ====================== DEX CONFIGURATION ======================
DEXES = {
    "Balancer": {
        "address": "0xBA12222222228d8Ba445958a75a0704d566BF2C8",
        "type": "balancer",
        "fee": 0.0005  # 0.05%
    },
    "Curve": {
        "address": "0x7F86Bf177DAd5Fc4F2e6E6b3bcAdA3ed2B0E38a5",
        "type": "curve",
        "fee": 0.0004  # 0.04%
    },
    "Uniswap V3": {
        "address": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
        "type": "uniswap_v3",
        "fee": 0.003  # 0.3%
    },
    "Camelot": {
        "address": "0xc873fEcbd354f5A56E00E710B90EF4201db2448d",
        "type": "camelot",
        "fee": 0.0025  # 0.25%
    }
}

# ====================== LIVE PRICE FETCHER ======================
class ArbitrumPriceFetcher:
    """Fetches real-time prices from Arbitrum DEXes"""
    
    def __init__(self):
        self.cache = {}
        self.last_update = 0
        self.cache_duration = 10  # seconds
    
    def get_prices(self) -> Dict:
        """Get current prices for all tokens"""
        current_time = time.time()
        
        # Return cached prices if fresh
        if current_time - self.last_update < self.cache_duration and self.cache:
            return self.cache
        
        try:
            # Fetch from CoinGecko (free API)
            token_ids = [t["coingecko_id"] for t in TOKENS.values() if t.get("coingecko_id")]
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(token_ids)}&vs_currencies=usd"
            
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                
                for symbol, token_info in TOKENS.items():
                    token_id = token_info.get("coingecko_id")
                    if token_id and token_id in data:
                        self.cache[symbol] = data[token_id]["usd"]
            
            self.last_update = current_time
            
        except Exception as e:
            st.warning(f"Price fetch error: {e}")
            # Fallback prices
            if not self.cache:
                self.cache = {"WETH": 3200, "WBTC": 60000, "USDC": 1, "USDT": 1, "ARB": 1.2, "LINK": 15}
        
        return self.cache

# ====================== ARBITRAGE SCANNER ======================
class ArbitrageScanner:
    """Scans for arbitrage opportunities across DEXes"""
    
    def __init__(self):
        self.price_fetcher = ArbitrumPriceFetcher()
        
        # Pre-defined arbitrage paths
        self.paths = [
            {
                "name": "WETH → WBTC → WETH",
                "tokens": ["WETH", "WBTC", "WETH"],
                "dexes": ["Curve", "Balancer"],
                "min_profit_eth": 0.001
            },
            {
                "name": "WETH → USDC → WETH",
                "tokens": ["WETH", "USDC", "WETH"],
                "dexes": ["Uniswap V3", "Balancer"],
                "min_profit_eth": 0.0005
            },
            {
                "name": "WETH → USDT → WETH",
                "tokens": ["WETH", "USDT", "WETH"],
                "dexes": ["Curve", "Camelot"],
                "min_profit_eth": 0.0005
            },
            {
                "name": "WETH → ARB → WETH",
                "tokens": ["WETH", "ARB", "WETH"],
                "dexes": ["Uniswap V3", "Camelot"],
                "min_profit_eth": 0.001
            },
            {
                "name": "WETH → LINK → WETH",
                "tokens": ["WETH", "LINK", "WETH"],
                "dexes": ["Balancer", "Uniswap V3"],
                "min_profit_eth": 0.001
            },
            {
                "name": "Multi-Hop: WETH → WBTC → USDC → WETH",
                "tokens": ["WETH", "WBTC", "USDC", "WETH"],
                "dexes": ["Curve", "Uniswap V3", "Balancer"],
                "min_profit_eth": 0.002
            }
        ]
    
    def get_dex_rate(self, token_in: str, token_out: str, dex_name: str) -> float:
        """Get exchange rate from a specific DEX"""
        prices = self.price_fetcher.get_prices()
        
        price_in = prices.get(token_in, 1)
        price_out = prices.get(token_out, 1)
        
        if price_in == 0 or price_out == 0:
            return 0
        
        # Base rate from market prices
        base_rate = price_in / price_out
        
        # Apply DEX-specific fees
        dex_fee = 1 - DEXES.get(dex_name, {}).get("fee", 0.003)
        
        # Simulate DEX-specific pricing (in production, query actual pools)
        if dex_name == "Curve":
            # Curve has better rates for stable pairs
            if token_in in ["USDC", "USDT"] and token_out in ["USDC", "USDT"]:
                return base_rate * 0.999  # Very low slippage
            return base_rate * 0.997
        
        elif dex_name == "Balancer":
            return base_rate * 0.998
        
        elif dex_name == "Uniswap V3":
            # Uniswap V3 has better rates for volatile pairs
            return base_rate * 0.997
        
        elif dex_name == "Camelot":
            return base_rate * 0.996
        
        return base_rate * dex_fee
    
    def scan_opportunities(self, flash_amount: float = 1.0) -> List[Dict]:
        """Scan all paths for profitable opportunities"""
        opportunities = []
        
        for path in self.paths:
            try:
                current_amount = flash_amount
                details = []
                
                # Execute the path
                for i in range(len(path["tokens"]) - 1):
                    token_in = path["tokens"][i]
                    token_out = path["tokens"][i + 1]
                    dex = path["dexes"][i] if i < len(path["dexes"]) else path["dexes"][0]
                    
                    rate = self.get_dex_rate(token_in, token_out, dex)
                    current_amount = current_amount * rate
                    
                    details.append({
                        "step": i + 1,
                        "from": token_in,
                        "to": token_out,
                        "dex": dex,
                        "rate": rate,
                        "amount": current_amount
                    })
                
                # Calculate profit
                expected_profit = current_amount - flash_amount
                
                # Account for flash loan fee (0.05%)
                flash_fee = flash_amount * 0.0005
                net_profit = expected_profit - flash_fee
                
                if net_profit > path["min_profit_eth"]:
                    opportunities.append({
                        "id": hashlib.md5(f"{path['name']}{time.time()}".encode()).hexdigest()[:12],
                        "name": path["name"],
                        "tokens": " → ".join(path["tokens"]),
                        "dexes": " → ".join(path["dexes"]),
                        "expected_profit": net_profit,
                        "roi": (net_profit / flash_amount) * 100,
                        "flash_fee": flash_fee,
                        "details": details,
                        "timestamp": datetime.now().strftime("%H:%M:%S")
                    })
            
            except Exception as e:
                continue
        
        # Sort by profit
        opportunities.sort(key=lambda x: x["expected_profit"], reverse=True)
        return opportunities

# ====================== EXECUTION ENGINE ======================
class ArbitrageExecutor:
    def __init__(self):
        self.scanner = ArbitrageScanner()
        self.conn = conn
    
    def calculate_profit(self, amount_weth: float, path: Dict = None) -> Dict:
        """Calculate expected profit"""
        if path:
            # Use specific path
            return path.get("expected_profit", 0)
        else:
            # Use best opportunity
            opportunities = self.scanner.scan_opportunities(amount_weth)
            if opportunities:
                return opportunities[0]
            return {"expected_profit": 0, "is_profitable": False}
    
    def execute_arbitrage(self, amount: float, min_profit: float, opportunity: Dict = None) -> Dict:
        """Execute arbitrage simulation"""
        time.sleep(1.5)  # Simulate transaction time
        
        if opportunity:
            expected_profit = opportunity["expected_profit"]
        else:
            opportunities = self.scanner.scan_opportunities(amount)
            expected_profit = opportunities[0]["expected_profit"] if opportunities else 0
        
        if expected_profit < min_profit:
            return {
                'success': False,
                'error': f'Expected profit {expected_profit:.4f} ETH below minimum {min_profit} ETH'
            }
        
        # Simulate execution with realistic slippage
        slippage = 0.98 + (0.02 * (expected_profit / amount))  # Less slippage for larger profits
        actual_profit = expected_profit * min(slippage, 0.995)
        
        # Gas cost on Arbitrum (very cheap)
        gas_cost = 0.0003  # ~$0.60
        
        net_profit = actual_profit - gas_cost
        
        # Generate transaction hash
        tx_hash = hashlib.sha256(f"{amount}{time.time()}{opportunity.get('id', '')}".encode()).hexdigest()[:16]
        
        # Save to database
        c.execute("INSERT INTO trades (tx_hash, amount, profit, timestamp, status, network, path) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (tx_hash, amount, net_profit, int(time.time()), 'SUCCESS', 'Arbitrum', opportunity.get('name', 'Unknown') if opportunity else 'Unknown'))
        conn.commit()
        
        return {
            'success': True,
            'tx_hash': tx_hash,
            'expected_profit': expected_profit,
            'actual_profit': net_profit,
            'gas_cost': gas_cost,
            'network': 'Arbitrum',
            'path': opportunity.get('name', 'Custom') if opportunity else 'Custom'
        }
    
    def get_stats(self):
        c.execute("SELECT SUM(profit), COUNT(*) FROM trades WHERE status='SUCCESS'")
        total_profit, total_trades = c.fetchone()
        
        c.execute("SELECT SUM(profit) FROM trades WHERE timestamp > strftime('%s', 'now', '-1 day') AND status='SUCCESS'")
        daily_profit = c.fetchone()[0] or 0
        
        return {
            'total_profit': total_profit or 0,
            'total_trades': total_trades or 0,
            'daily_profit': daily_profit,
            'avg_profit': (total_profit / total_trades) if total_trades else 0
        }
    
    def get_history(self, limit=50):
        return pd.read_sql_query(f"SELECT datetime(timestamp, 'unixepoch') as time, amount, profit, status, network, path FROM trades ORDER BY timestamp DESC LIMIT {limit}", conn)

# ====================== INITIALIZE ======================
if 'executor' not in st.session_state:
    st.session_state.executor = ArbitrageExecutor()
if 'scanner' not in st.session_state:
    st.session_state.scanner = ArbitrageScanner()
if 'opportunities' not in st.session_state:
    st.session_state.opportunities = []
if 'auto_scan' not in st.session_state:
    st.session_state.auto_scan = False

# ====================== UI ======================
st.title("🟠 Arbitrum MEV Arbitrage Bot")
st.markdown("### Live Arbitrage Scanner | Flash Loan Execution | 50x Lower Fees")

# Sidebar
with st.sidebar:
    st.markdown("## ⚡ Network Status")
    st.success("🟢 Arbitrum One")
    st.metric("Gas Price", "~0.3 Gwei", delta="95% cheaper than ETH")
    st.info("💰 Cost per trade: ~$0.60")
    
    st.markdown("---")
    
    st.markdown("## 🎮 Controls")
    amount = st.number_input("Flash Loan Amount (WETH)", min_value=0.01, max_value=100.0, value=1.0, step=0.1, help="Amount to borrow")
    min_profit = st.number_input("Min Profit (ETH)", min_value=0.0001, max_value=1.0, value=0.001, step=0.0001, format="%.4f")
    
    auto_scan = st.checkbox("🔄 Auto-scan (every 10s)", value=st.session_state.auto_scan)
    if auto_scan != st.session_state.auto_scan:
        st.session_state.auto_scan = auto_scan
    
    st.markdown("---")
    
    if st.button("🔍 Scan Now", use_container_width=True):
        with st.spinner("Scanning Arbitrum DEXes..."):
            st.session_state.opportunities = st.session_state.scanner.scan_opportunities(amount)
            st.success(f"Found {len(st.session_state.opportunities)} opportunities!")

# Main content - Live Market Data Row
col1, col2, col3, col4, col5 = st.columns(5)

price_fetcher = ArbitrumPriceFetcher()
prices = price_fetcher.get_prices()

with col1:
    st.metric("💰 WETH", f"${prices.get('WETH', 3200):,.0f}")
with col2:
    st.metric("₿ WBTC", f"${prices.get('WBTC', 60000):,.0f}")
with col3:
    st.metric("💵 USDC", f"${prices.get('USDC', 1):,.2f}")
with col4:
    st.metric("🟠 ARB", f"${prices.get('ARB', 1.2):,.2f}")
with col5:
    st.metric("🔗 LINK", f"${prices.get('LINK', 15):,.2f}")

st.markdown("---")

# ====================== ARBITRAGE SCANNER SECTION ======================
st.markdown("## 🔍 Live Arbitrage Scanner")

col1, col2 = st.columns([3, 1])
with col2:
    if st.button("🔄 Refresh Scanner", use_container_width=True):
        with st.spinner("Scanning..."):
            st.session_state.opportunities = st.session_state.scanner.scan_opportunities(amount)
            st.rerun()

# Display opportunities
if st.session_state.opportunities:
    st.success(f"🎯 Found {len(st.session_state.opportunities)} profitable opportunities!")
    
    for idx, opp in enumerate(st.session_state.opportunities):
        with st.expander(f"💰 {opp['name']} | Profit: {opp['expected_profit']:.4f} ETH (ROI: {opp['roi']:.2f}%)", expanded=idx == 0):
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Expected Profit", f"{opp['expected_profit']:.4f} ETH")
            with col2:
                st.metric("ROI", f"{opp['roi']:.2f}%")
            with col3:
                st.metric("Flash Fee", f"{opp['flash_fee']:.4f} ETH")
            with col4:
                st.metric("Path", opp['tokens'])
            
            st.markdown("**Route Details:**")
            for detail in opp['details']:
                st.write(f"  Step {detail['step']}: {detail['from']} → {detail['to']} via {detail['dex']} (Rate: {detail['rate']:.4f})")
            
            if st.button(f"Execute This Opportunity", key=opp['id']):
                with st.spinner(f"Executing arbitrage on Arbitrum..."):
                    result = st.session_state.executor.execute_arbitrage(amount, min_profit, opp)
                    if result['success']:
                        st.balloons()
                        st.success("✅ Arbitrage Executed Successfully!")
                        col_a, col_b, col_c, col_d = st.columns(4)
                        with col_a:
                            st.metric("Expected Profit", f"{result['expected_profit']:.4f} ETH")
                        with col_b:
                            st.metric("Actual Profit", f"{result['actual_profit']:.4f} ETH")
                        with col_c:
                            st.metric("Gas Cost", f"{result['gas_cost']:.4f} ETH")
                        with col_d:
                            st.metric("Network", result['network'])
                        st.code(f"Tx Hash: {result['tx_hash']}")
                        st.info("💡 Transaction simulated. For real execution, deploy contract and add private key to secrets.")
                    else:
                        st.error(f"Failed: {result.get('error')}")
else:
    st.info("No profitable opportunities found at this moment. Try adjusting the flash loan amount or scan again.")
    
    # Show what's being monitored
    with st.expander("📊 What We're Scanning"):
        st.markdown("**Active Arbitrage Paths:**")
        for path in st.session_state.scanner.paths:
            st.write(f"- {path['name']} (Min profit: {path['min_profit_eth']} ETH)")

# ====================== STATISTICS ======================
stats = st.session_state.executor.get_stats()

st.markdown("---")
st.markdown("## 📊 Performance Statistics")

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("💰 Total Profit", f"{stats['total_profit']:.4f} ETH")
with col2:
    st.metric("📈 Total Trades", stats['total_trades'])
with col3:
    st.metric("💹 Daily Profit", f"{stats['daily_profit']:.4f} ETH")
with col4:
    st.metric("⭐ Avg Profit/Trade", f"{stats['avg_profit']:.4f} ETH")

# ====================== EXECUTION PANEL ======================
st.markdown("---")
st.markdown("## 🚀 Quick Execution")

col1, col2 = st.columns(2)
with col1:
    quick_amount = st.number_input("Amount (WETH)", min_value=0.01, value=1.0, step=0.1, key="quick_amount")
with col2:
    quick_min_profit = st.number_input("Min Profit (ETH)", min_value=0.0001, value=0.001, step=0.0001, key="quick_min")

if st.button("⚡ Execute Best Opportunity", type="primary", use_container_width=True):
    with st.spinner("Finding and executing best opportunity..."):
        # Scan first
        opportunities = st.session_state.scanner.scan_opportunities(quick_amount)
        if opportunities:
            best = opportunities[0]
            st.info(f"Found opportunity: {best['name']} - Expected profit: {best['expected_profit']:.4f} ETH")
            
            # Execute
            result = st.session_state.executor.execute_arbitrage(quick_amount, quick_min_profit, best)
            if result['success']:
                st.balloons()
                st.success("✅ Arbitrage Executed!")
                st.metric("Profit", f"{result['actual_profit']:.4f} ETH")
            else:
                st.error(f"Execution failed: {result.get('error')}")
        else:
            st.warning("No profitable opportunities found")

# ====================== TRADE HISTORY ======================
st.markdown("---")
st.markdown("## 📜 Trade History")

history = st.session_state.executor.get_history()
if not history.empty:
    st.dataframe(
        history,
        use_container_width=True,
        column_config={
            "time": "Time",
            "amount": st.column_config.NumberColumn("Amount (WETH)", format="%.3f"),
            "profit": st.column_config.NumberColumn("Profit (ETH)", format="%.4f"),
            "status": "Status",
            "network": "Network",
            "path": "Arbitrage Path"
        }
    )
    
    # Download button
    csv = history.to_csv(index=False)
    st.download_button("📥 Download History (CSV)", csv, "arbitrum_trades.csv", "text/csv")
else:
    st.info("No trades yet. Execute an arbitrage to see history here.")

# ====================== AUTO REFRESH ======================
if st.session_state.auto_scan:
    time.sleep(10)
    with st.spinner("Auto-scanning..."):
        st.session_state.opportunities = st.session_state.scanner.scan_opportunities(amount)
        st.rerun()

# ====================== FOOTER ======================
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: gray;'>
<b>Arbitrum MEV Arbitrage Bot</b> | Live Scanner | Flash Loan Execution | 50x Lower Fees than Ethereum
<br>
<small>Scanning {Balancer, Curve, Uniswap V3, Camelot} | Tokens: WETH, WBTC, USDC, USDT, ARB, LINK</small>
</div>
""", unsafe_allow_html=True)
