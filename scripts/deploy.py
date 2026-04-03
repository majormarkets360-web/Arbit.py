from web3 import Web3
import json
import os

# Arbitrum Mainnet RPC (use your own Alchemy/Infura key)
ARBITRUM_RPC = "https://arb1.arbitrum.io/rpc"

# Load your private key from environment
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "YOUR_PRIVATE_KEY_HERE")

def deploy_contract():
    w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC))
    
    if not w3.is_connected():
        print("Failed to connect to Arbitrum")
        return
    
    account = w3.eth.account.from_key(PRIVATE_KEY)
    print(f"Deploying from: {account.address}")
    print(f"Balance: {w3.from_wei(w3.eth.get_balance(account.address), 'ether')} ETH")
    
    # Contract bytecode (compile with Remix first)
    bytecode = "0x..."  # Paste your compiled bytecode here
    abi = json.loads('[...]')  # Paste your contract ABI here
    
    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    
    # Build transaction
    tx = contract.constructor().build_transaction({
        'from': account.address,
        'nonce': w3.eth.get_transaction_count(account.address),
        'gas': 3000000,
        'gasPrice': w3.eth.gas_price,
        'chainId': 42161  # Arbitrum chain ID
    })
    
    # Sign and send
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    
    print(f"Deployment tx: {tx_hash.hex()}")
    print("Waiting for confirmation...")
    
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"Contract deployed at: {receipt.contractAddress}")

if __name__ == "__main__":
    deploy_contract()
