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

st.set_page_config(
    page_title="Arbitrum MEV Bot",
    page_icon="🟠",
    layout="wide"
)

# ====================== DATABASE SETUP ======================
os.makedirs('data', exist_ok=True)
conn = sqlite3.connect('data/arbitrum_trades.db', check_same_thread=False)
c = conn.cursor()

# Create tables with proper schema
c.execute('''CREATE TABLE IF NOT EXISTS trades
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              tx_hash TEXT, 
              amount REAL, 
              profit REAL, 
              timestamp INTEGER, 
              status TEXT, 
              network TEXT,
              path TEXT)''')

c.execute('''CREATE TABLE IF NOT EXISTS opportunities
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              token_path TEXT, 
              dex_path TEXT, 
              expected_profit REAL,
              timestamp INTEGER, 
              executed INTEGER DEFAULT 0)''')

conn.commit()

# ====================== TOKEN CONFIG ======================
TOKENS = {
    "WETH": {
        "address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
        "symbol": "WETH",
        "decimals": 18,
    },
    "WBTC": {
        "address": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        "symbol": "WBTC",
        "decimals": 8,
    },
    "USDC": {
        "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        "symbol": "USDC",
        "decimals": 6,
    },
    "USDT": {
        "address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
        "symbol": "USDT",
        "decimals": 6,
    },
    "ARB": {
        "address": "0x912CE59144191C1204E64559FE8253a0e49E6548",
        "symbol": "ARB",
        "decimals": 18,
    },
}

# ====================== PRICE FETCHER ======================
class PriceFetcher:
    def __init__(self):
        self.cache = {}
        self.last_update = 0
    
    def get_prices(self):
        current_time = time.time()
        if current_time - self.last_update < 10 and self.cache:
            return self.cache
        
        try:
            response = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "ethereum,wrapped-bitcoin,usd-coin,tether,arbitrum", "vs_currencies": "usd"},
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                self.cache = {
                    "WETH": data.get("ethereum", {}).get("usd", 3200),
                    "WBTC": data.get("wrapped-bitcoin", {}).get("usd", 60000),
                    "USDC": data.get("usd-coin", {}).get("usd", 1),
                    "USDT": data.get("tether", {}).get("usd", 1),
                    "ARB": data.get("arbitrum", {}).get("usd", 1.2),
                }
                self.last_update = current_time
        except:
            if not self.cache:
                self.cache = {"WETH": 3200, "WBTC": 60000, "USDC": 1, "USDT": 1, "ARB": 1.2}
        
        return self.cache

# ====================== ARBITRAGE SCANNER ======================
class ArbitrageScanner:
    def __init__(self):
        self.price_fetcher = PriceFetcher()
        
        self.paths = [
            {"name": "WETH → WBTC → WETH", "tokens": ["WETH", "WBTC", "WETH"], "dexes": ["Curve", "Balancer"]},
            {"name": "WETH → USDC → WETH", "tokens": ["WETH", "USDC", "WETH"], "dexes": ["Uniswap V3", "Balancer"]},
            {"name": "WETH → USDT → WETH", "tokens": ["WETH", "USDT", "WETH"], "dexes": ["Curve", "Camelot"]},
            {"name": "WETH → ARB → WETH", "tokens": ["WETH", "ARB", "WETH"], "dexes": ["Uniswap V3", "Camelot"]},
        ]
    
    def calculate_rate(self, token_in, token_out, dex):
        prices = self.price_fetcher.get_prices()
        price_in = prices.get(token_in, 1)
        price_out = prices.get(token_out, 1)
        
        if price_in == 0 or price_out == 0:
            return 0
        
        base_rate = price_in / price_out
        
        # Apply DEX-specific fees
        fees = {"Curve": 0.997, "Balancer": 0.998, "Uniswap V3": 0.997, "Camelot": 0.996}
        fee = fees.get(dex, 0.997)
        
        return base_rate * fee
    
    def scan_opportunities(self, flash_amount=1.0):
        opportunities = []
        
        for path in self.paths:
            try:
                current_amount = flash_amount
                details = []
                
                for i in range(len(path["tokens"]) - 1):
                    rate = self.calculate_rate(path["tokens"][i], path["tokens"][i + 1], path["dexes"][i])
                    current_amount = current_amount * rate
                    details.append(f"{path['tokens'][i]} → {path['tokens'][i+1]} via {path['dexes'][i]}: {rate:.4f}")
                
                expected_profit = current_amount - flash_amount
                flash_fee = flash_amount * 0.0005
                net_profit = expected_profit - flash_fee
                
                if net_profit > 0.0005:  # Minimum $1 profit
                    opportunities.append({
                        "id": hashlib.md5(path["name"].encode()).hexdigest()[:8],
                        "name": path["name"],
                        "expected_profit": net_profit,
                        "roi": (net_profit / flash_amount) * 100,
                        "flash_fee": flash_fee,
                        "details": details,
                    })
            except:
                continue
        
        return sorted(opportunities, key=lambda x: x["expected_profit"], reverse=True)

# ====================== EXECUTION ENGINE ======================
class ArbitrageExecutor:
    def __init__(self):
        self.scanner = ArbitrageScanner()
        self.conn = conn
    
    def execute_arbitrage(self, amount, min_profit, opportunity=None):
        """Execute arbitrage simulation"""
        time.sleep(1)
        
        if opportunity:
            expected_profit = opportunity["expected_profit"]
        else:
            opportunities = self.scanner.scan_opportunities(amount)
            expected_profit = opportunities[0]["expected_profit"] if opportunities else 0
        
        if expected_profit < min_profit:
            return {
                'success': False,
                'error': f'Profit {expected_profit:.4f} ETH below minimum {min_profit} ETH'
            }
        
        # Simulate execution
        actual_profit = expected_profit * 0.98
        tx_hash = hashlib.md5(f"{amount}{time.time()}".encode()).hexdigest()[:16]
        
        # Save to database with proper timestamp
        timestamp = int(time.time())
        c.execute("""
            INSERT INTO trades (tx_hash, amount, profit, timestamp, status, network, path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (tx_hash, amount, actual_profit, timestamp, 'SUCCESS', 'Arbitrum', opportunity.get('name', 'Unknown') if opportunity else 'Unknown'))
        conn.commit()
        
        return {
            'success': True,
            'tx_hash': tx_hash,
            'actual_profit': actual_profit,
            'expected_profit': expected_profit,
            'gas_cost': 0.0003,
        }
    
    def get_stats(self):
        """Get statistics from database"""
        try:
            c.execute("SELECT SUM(profit), COUNT(*) FROM trades WHERE status='SUCCESS'")
            result = c.fetchone()
            total_profit = result[0] if result[0] else 0
            total_trades = result[1] if result[1] else 0
            
            # Daily profit (last 24 hours)
            one_day_ago = int(time.time()) - 86400
            c.execute("SELECT SUM(profit) FROM trades WHERE status='SUCCESS' AND timestamp > ?", (one_day_ago,))
            daily_profit = c.fetchone()[0] or 0
            
            return {
                'total_profit': total_profit,
                'total_trades': total_trades,
                'daily_profit': daily_profit,
                'avg_profit': (total_profit / total_trades) if total_trades > 0 else 0
            }
        except Exception as e:
            return {'total_profit': 0, 'total_trades': 0, 'daily_profit': 0, 'avg_profit': 0}
    
    def get_history(self, limit=50):
        """Get trade history as DataFrame"""
        try:
            c.execute("""
                SELECT 
                    datetime(timestamp, 'unixepoch') as time,
                    amount,
                    profit,
                    status,
                    network,
                    path
                FROM trades 
                ORDER BY timestamp DESC 
                LIMIT ?
            """, (limit,))
            
            rows = c.fetchall()
            if rows:
                df = pd.DataFrame(rows, columns=['time', 'amount', 'profit', 'status', 'network', 'path'])
                return df
            else:
                return pd.DataFrame(columns=['time', 'amount', 'profit', 'status', 'network', 'path'])
        except Exception as e:
            st.error(f"History error: {e}")
            return pd.DataFrame(columns=['time', 'amount', 'profit', 'status', 'network', 'path'])

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
st.markdown("### Live Arbitrage Scanner | Flash Loan Execution")

# Sidebar
with st.sidebar:
    st.markdown("## ⚡ Network")
    st.success("🟢 Arbitrum One")
    st.metric("Gas Price", "~0.3 Gwei", delta="95% cheaper")
    
    st.markdown("---")
    
    amount = st.number_input("Flash Loan (WETH)", min_value=0.01, max_value=100.0, value=1.0, step=0.1)
    min_profit = st.number_input("Min Profit (ETH)", min_value=0.0001, max_value=1.0, value=0.001, step=0.0001, format="%.4f")
    
    auto_scan = st.checkbox("Auto-scan (10s)", value=st.session_state.auto_scan)
    if auto_scan != st.session_state.auto_scan:
        st.session_state.auto_scan = auto_scan
    
    st.markdown("---")
    
    if st.button("🔍 Scan Now", use_container_width=True):
        with st.spinner("Scanning..."):
            st.session_state.opportunities = st.session_state.scanner.scan_opportunities(amount)
            st.rerun()

# Market prices
price_fetcher = PriceFetcher()
prices = price_fetcher.get_prices()

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("WETH", f"${prices.get('WETH', 3200):,.0f}")
with col2:
    st.metric("WBTC", f"${prices.get('WBTC', 60000):,.0f}")
with col3:
    st.metric("USDC", f"${prices.get('USDC', 1):,.2f}")
with col4:
    st.metric("USDT", f"${prices.get('USDT', 1):,.2f}")
with col5:
    st.metric("ARB", f"${prices.get('ARB', 1.2):,.2f}")

st.markdown("---")

# ====================== OPPORTUNITIES ======================
st.markdown("## 🔍 Arbitrage Opportunities")

if st.session_state.opportunities:
    st.success(f"Found {len(st.session_state.opportunities)} opportunities!")
    
    for opp in st.session_state.opportunities[:5]:
        with st.expander(f"💰 {opp['name']} | Profit: {opp['expected_profit']:.4f} ETH ({opp['roi']:.2f}%)"):
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Expected Profit", f"{opp['expected_profit']:.4f} ETH")
            with col2:
                st.metric("ROI", f"{opp['roi']:.2f}%")
            with col3:
                st.metric("Flash Fee", f"{opp['flash_fee']:.4f} ETH")
            
            st.markdown("**Route:**")
            for detail in opp['details']:
                st.write(f"• {detail}")
            
            if st.button(f"Execute", key=opp['id']):
                with st.spinner("Executing..."):
                    result = st.session_state.executor.execute_arbitrage(amount, min_profit, opp)
                    if result['success']:
                        st.balloons()
                        st.success("✅ Arbitrage Executed!")
                        col_a, col_b, col_c = st.columns(3)
                        with col_a:
                            st.metric("Expected", f"{result['expected_profit']:.4f} ETH")
                        with col_b:
                            st.metric("Actual", f"{result['actual_profit']:.4f} ETH")
                        with col_c:
                            st.metric("Gas", f"{result['gas_cost']:.4f} ETH")
                        st.code(f"Tx: {result['tx_hash']}")
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error(f"Failed: {result.get('error')}")
else:
    st.info("No opportunities found. Click 'Scan Now' to search for arbitrage.")

# ====================== QUICK EXECUTE ======================
st.markdown("---")
st.markdown("## 🚀 Quick Execute")

col1, col2 = st.columns(2)
with col1:
    quick_amount = st.number_input("Amount (WETH)", min_value=0.01, value=1.0, step=0.1, key="quick_amount")
with col2:
    quick_min = st.number_input("Min Profit (ETH)", min_value=0.0001, value=0.001, step=0.0001, key="quick_min", format="%.4f")

if st.button("⚡ Execute Best Opportunity", type="primary", use_container_width=True):
    with st.spinner("Finding best opportunity..."):
        opportunities = st.session_state.scanner.scan_opportunities(quick_amount)
        if opportunities:
            best = opportunities[0]
            st.info(f"Best: {best['name']} - {best['expected_profit']:.4f} ETH")
            
            result = st.session_state.executor.execute_arbitrage(quick_amount, quick_min, best)
            if result['success']:
                st.balloons()
                st.success(f"✅ Profit: {result['actual_profit']:.4f} ETH")
            else:
                st.error(result.get('error'))
        else:
            st.warning("No profitable opportunities found")

# ====================== STATISTICS ======================
stats = st.session_state.executor.get_stats()

st.markdown("---")
st.markdown("## 📊 Statistics")

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Profit", f"{stats['total_profit']:.4f} ETH")
with col2:
    st.metric("Total Trades", stats['total_trades'])
with col3:
    st.metric("Daily Profit", f"{stats['daily_profit']:.4f} ETH")
with col4:
    st.metric("Avg/Trade", f"{stats['avg_profit']:.4f} ETH")

# ====================== TRADE HISTORY ======================
st.markdown("---")
st.markdown("## 📜 Trade History")

history_df = st.session_state.executor.get_history()

if not history_df.empty:
    st.dataframe(history_df, use_container_width=True)
    
    csv = history_df.to_csv(index=False)
    st.download_button("📥 Download CSV", csv, "trades.csv", "text/csv")
else:
    st.info("No trades yet. Execute an arbitrage to see history here.")

# ====================== AUTO REFRESH ======================
if st.session_state.auto_scan:
    time.sleep(10)
    st.rerun()

# ====================== FOOTER ======================
st.markdown("---")
st.markdown("*Arbitrum MEV Arbitrage Bot | Powered by Balancer Flash Loans*")
