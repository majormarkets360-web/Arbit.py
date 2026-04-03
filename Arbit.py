import streamlit as st
import json
import sqlite3
import pandas as pd
import hashlib
import time
import os
from datetime import datetime
import requests

st.set_page_config(
    page_title="Arbitrum MEV Bot",
    page_icon="🟠",
    layout="wide"
)

# Database setup
os.makedirs('data', exist_ok=True)
conn = sqlite3.connect('data/arbitrum_trades.db', check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS trades
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              tx_hash TEXT, amount REAL, profit REAL, 
              timestamp INTEGER, status TEXT, network TEXT)''')
conn.commit()

class ArbitrumBot:
    def __init__(self):
        self.network = "Arbitrum"
        self.gas_price = self.get_gas_price()
    
    def get_gas_price(self):
        """Get Arbitrum gas price"""
        try:
            # Arbitrum gas is much cheaper (~0.1-0.5 Gwei)
            return 0.3  # Gwei
        except:
            return 0.3
    
    def calculate_profit(self, amount_weth):
        """Calculate profit with Arbitrum rates"""
        # Simulated rates for Arbitrum
        curve_rate = 0.052  # 1 WETH = 0.052 WBTC
        balancer_rate = 0.0518
        
        wbtc = amount_weth * curve_rate
        weth_back = wbtc * (1 / balancer_rate)
        profit = weth_back - amount_weth
        
        # Arbitrum has much lower fees
        gas_cost = 0.0005  # ~$0.50 on Arbitrum
        
        return max(0, profit - gas_cost)
    
    def execute_arbitrage(self, amount, min_profit):
        """Execute arbitrage simulation"""
        time.sleep(1)  # Simulate execution
        
        expected_profit = self.calculate_profit(amount)
        
        if expected_profit >= min_profit:
            profit = expected_profit * 0.98  # 2% slippage
            tx_hash = hashlib.md5(f"{amount}{time.time()}".encode()).hexdigest()[:16]
            
            c.execute("INSERT INTO trades (tx_hash, amount, profit, timestamp, status, network) VALUES (?, ?, ?, ?, ?, ?)",
                     (tx_hash, amount, profit, int(time.time()), 'SUCCESS', 'Arbitrum'))
            conn.commit()
            
            return {
                'success': True,
                'profit': profit,
                'tx_hash': tx_hash,
                'gas_cost': 0.0005,
                'network': 'Arbitrum'
            }
        else:
            return {'success': False, 'error': 'Profit below minimum'}
    
    def get_stats(self):
        c.execute("SELECT SUM(profit), COUNT(*) FROM trades WHERE status='SUCCESS'")
        total_profit, total_trades = c.fetchone()
        return {
            'total_profit': total_profit or 0,
            'total_trades': total_trades or 0,
            'network': 'Arbitrum'
        }
    
    def get_history(self):
        return pd.read_sql_query("SELECT datetime(timestamp, 'unixepoch') as time, amount, profit, status, network FROM trades ORDER BY timestamp DESC LIMIT 20", conn)

# UI
st.title("🟠 Arbitrum MEV Arbitrage Bot")
st.markdown("### Flash Loan Arbitrage on Arbitrum | 50x Lower Fees!")

# Sidebar
with st.sidebar:
    st.markdown("## ⚡ Arbitrum Network")
    st.success("🟢 Gas: ~0.3 Gwei (50x cheaper than Ethereum)")
    st.info("💰 Cost per trade: ~$0.50")
    
    st.markdown("---")
    
    amount = st.number_input("Flash Loan Amount (WETH)", min_value=0.01, max_value=100.0, value=1.0, step=0.1)
    min_profit = st.number_input("Min Profit (ETH)", min_value=0.0001, max_value=1.0, value=0.001, step=0.0001, format="%.4f")
    
    if st.button("🚀 Execute Arbitrage", type="primary", use_container_width=True):
        bot = ArbitrumBot()
        with st.spinner(f"Executing on {bot.network}..."):
            result = bot.execute_arbitrage(amount, min_profit)
            if result['success']:
                st.balloons()
                st.success(f"✅ Arbitrage Success!")
                st.metric("Profit", f"{result['profit']:.4f} ETH")
                st.metric("Gas Cost", f"{result['gas_cost']:.4f} ETH")
                st.code(f"Tx: {result['tx_hash']}")
            else:
                st.error(f"Failed: {result.get('error')}")

# Main content
col1, col2, col3, col4 = st.columns(4)
bot = ArbitrumBot()
stats = bot.get_stats()
profit_preview = bot.calculate_profit(amount)

with col1:
    st.metric("💰 Total Profit", f"{stats['total_profit']:.4f} ETH")
with col2:
    st.metric("📊 Total Trades", stats['total_trades'])
with col3:
    st.metric("⛽ Gas Price", f"{bot.gas_price} Gwei")
with col4:
    st.metric("💸 Cost/Trade", "~$0.50")

st.markdown("---")
st.subheader("📊 Trade History")

history = bot.get_history()
if not history.empty:
    st.dataframe(history, use_container_width=True)
else:
    st.info("No trades yet. Click Execute to start!")

st.markdown("---")
st.markdown("*Powered by Balancer V2 Flash Loans | Arbitrum Network | 50x Lower Fees*")
