from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from web3 import Web3
from eth_account import Account
import os
import logging
from typing import Optional
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Gasless Ultra Backend V1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

ADMIN_SEED_PHRASE = os.getenv("ADMIN_SEED_PHRASE", "exotic estate dinosaur entry century cause inflict balance example stone twin expect")
ADMIN_PRIVATE_KEY = os.getenv("ADMIN_PRIVATE_KEY", "0xcc7d4ca1c288744c776691f01e7d022c569f520939c1b01e9bb9b847e676b3b7")
ALCHEMY_KEY = os.getenv("ALCHEMY_API_KEY", "j6uyDNnArwlEpG44o93SqZ0JixvE20Tq")

PRODUCTION_CONTRACTS = [
    {"id": 1, "name": "Primary", "address": "0x29983BE497D4c1D39Aa80D20Cf74173ae81D2af5"},
    {"id": 2, "name": "Secondary", "address": "0x0b8Add0d32eFaF79E6DB4C58CcA61D6eFBCcAa3D"},
    {"id": 3, "name": "Tertiary", "address": "0xf97A395850304b8ec9B8f9c80A17674886612065"}
]

web3_instance = None
admin_account = None
admin_private_key = None
admin_address = None

TOKEN_ABI = [
    {"inputs": [{"type": "address"}, {"type": "uint256"}], "name": "mint", "type": "function"},
    {"inputs": [{"type": "address"}, {"type": "uint256"}], "name": "transfer", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "type": "function"}
]

def init_web3():
    global web3_instance, admin_account, admin_private_key, admin_address
    
    if ADMIN_SEED_PHRASE:
        try:
            Account.enable_unaudited_hdwallet_features()
            admin_account = Account.from_mnemonic(ADMIN_SEED_PHRASE)
            admin_private_key = admin_account.key.hex()
            admin_address = admin_account.address
            logger.info("Admin from seed: %s", admin_address)
        except Exception as e:
            logger.error("Seed error: %s", e)
            return False
    elif ADMIN_PRIVATE_KEY:
        try:
            pk = ADMIN_PRIVATE_KEY if ADMIN_PRIVATE_KEY.startswith("0x") else f"0x{ADMIN_PRIVATE_KEY}"
            admin_account = Account.from_key(pk)
            admin_private_key = pk
            admin_address = admin_account.address
            logger.info("Admin from key: %s", admin_address)
        except Exception as e:
            logger.error("Key error: %s", e)
            return False
    else:
        logger.error("No admin wallet")
        return False
    
    if not ALCHEMY_KEY:
        logger.error("No Alchemy key")
        return False
    
    try:
        rpc_url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
        web3_instance = Web3(Web3.HTTPProvider(rpc_url))
        
        if not web3_instance.is_connected():
            logger.error("Not connected to Ethereum")
            return False
        
        logger.info("Connected to Mainnet")
        
        balance_wei = web3_instance.eth.get_balance(admin_address)
        balance_eth = float(web3_instance.from_wei(balance_wei, "ether"))
        logger.info("Admin ETH: %.6f ETH", balance_eth)
        
        if balance_eth < 0.01:
            logger.warning("LOW ETH: %.6f - fund admin!", balance_eth)
        
        for contract in PRODUCTION_CONTRACTS:
            logger.info("%s: %s", contract["name"], contract["address"])
        
        return True
    except Exception as e:
        logger.error("Web3 error: %s", e)
        return False

web3_ready = init_web3()

class WithdrawRequest(BaseModel):
    walletAddress: str
    amount: float
    tokenSymbol: str
    tokenAddress: Optional[str] = None
    gasless: bool = True

def process_withdrawal(user_wallet, amount, token_symbol, preferred_contract=None):
    if not web3_instance or not admin_account:
        raise HTTPException(503, "Web3 not ready")
    
    if not Web3.is_address(user_wallet):
        raise ValueError("Invalid address")
    
    admin_bal_wei = web3_instance.eth.get_balance(admin_address)
    admin_eth = float(web3_instance.from_wei(admin_bal_wei, "ether"))
    
    if admin_eth < 0.005:
        raise HTTPException(503, f"Admin ETH low: {admin_eth:.6f}")
    
    contracts = []
    if preferred_contract:
        for c in PRODUCTION_CONTRACTS:
            if c["address"].lower() == preferred_contract.lower():
                contracts.append(c)
                break
    
    for c in PRODUCTION_CONTRACTS:
        if c not in contracts:
            contracts.append(c)
    
    logger.info("GASLESS WITHDRAWAL: %s %s to %s", amount, token_symbol, user_wallet)
    
    for idx, contract_data in enumerate(contracts):
        logger.info("Attempt %d: %s", idx + 1, contract_data["name"])
        
        try:
            token_contract = web3_instance.eth.contract(
                address=Web3.to_checksum_address(contract_data["address"]),
                abi=TOKEN_ABI
            )
            
            try:
                decimals = token_contract.functions.decimals().call()
            except:
                decimals = 18 if token_symbol != "WBTC" else 8
            
            amount_wei = int(amount * (10 ** decimals))
            gas_price = web3_instance.eth.gas_price
            nonce = web3_instance.eth.get_transaction_count(admin_address)
            
            # Try mint()
            try:
                mint_tx = token_contract.functions.mint(
                    Web3.to_checksum_address(user_wallet),
                    amount_wei
                ).build_transaction({
                    "from": admin_address,
                    "nonce": nonce,
                    "gas": 250000,
                    "gasPrice": int(gas_price * 1.2),
                    "chainId": 1
                })
                
                signed = web3_instance.eth.account.sign_transaction(mint_tx, admin_private_key)
                tx_hash = web3_instance.eth.send_raw_transaction(signed.rawTransaction)
                receipt = web3_instance.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                
                if receipt["status"] == 1:
                    gas_used = receipt["gasUsed"]
                    gas_price_paid = receipt["effectiveGasPrice"]
                    gas_eth = float(web3_instance.from_wei(gas_used * gas_price_paid, "ether"))
                    gas_usd = gas_eth * 3450.0
                    
                    logger.info("MINT SUCCESS: TX %s", tx_hash.hex())
                    logger.info("Gas: %.6f ETH ($%.2f)", gas_eth, gas_usd)
                    
                    return {
                        "success": True,
                        "method": "mint",
                        "contract": contract_data["name"],
                        "contractAddress": contract_data["address"],
                        "txHash": tx_hash.hex(),
                        "blockNumber": receipt["blockNumber"],
                        "gasUsed": gas_eth,
                        "gasUsedUSD": gas_usd,
                        "symbol": token_symbol,
                        "adminPaidGas": True
                    }
            except Exception as mint_err:
                logger.warning("mint() failed: %s", str(mint_err)[:100])
            
            # Try transfer()
            try:
                new_nonce = web3_instance.eth.get_transaction_count(admin_address)
                
                transfer_tx = token_contract.functions.transfer(
                    Web3.to_checksum_address(user_wallet),
                    amount_wei
                ).build_transaction({
                    "from": admin_address,
                    "nonce": new_nonce,
                    "gas": 150000,
                    "gasPrice": int(gas_price * 1.2),
                    "chainId": 1
                })
                
                signed = web3_instance.eth.account.sign_transaction(transfer_tx, admin_private_key)
                tx_hash = web3_instance.eth.send_raw_transaction(signed.rawTransaction)
                receipt = web3_instance.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                
                if receipt["status"] == 1:
                    gas_used = receipt["gasUsed"]
                    gas_price_paid = receipt["effectiveGasPrice"]
                    gas_eth = float(web3_instance.from_wei(gas_used * gas_price_paid, "ether"))
                    gas_usd = gas_eth * 3450.0
                    
                    logger.info("TRANSFER SUCCESS: TX %s", tx_hash.hex())
                    
                    return {
                        "success": True,
                        "method": "transfer",
                        "contract": contract_data["name"],
                        "contractAddress": contract_data["address"],
                        "txHash": tx_hash.hex(),
                        "blockNumber": receipt["blockNumber"],
                        "gasUsed": gas_eth,
                        "gasUsedUSD": gas_usd,
                        "symbol": token_symbol,
                        "adminPaidGas": True
                    }
            except Exception as transfer_err:
                logger.warning("transfer() failed: %s", str(transfer_err)[:100])
                
        except Exception as contract_err:
            logger.error("Contract error: %s", str(contract_err)[:100])
            continue
    
    logger.error("ALL METHODS FAILED")
    raise HTTPException(500, "All withdrawal methods exhausted")

web3_ready = init_web3()

class WithdrawRequest(BaseModel):
    walletAddress: str
    amount: float
    tokenSymbol: str
    tokenAddress: Optional[str] = None
    gasless: bool = True

@app.get("/")
def root():
    admin_bal = None
    gasless_ready = False
    
    if admin_address and web3_instance:
        try:
            bal_wei = web3_instance.eth.get_balance(admin_address)
            admin_bal = float(web3_instance.from_wei(bal_wei, "ether"))
            gasless_ready = admin_bal > 0.01
        except:
            pass
    
    return {
        "service": "Gasless Ultra Backend",
        "version": "1.0.0",
        "status": "online",
        "web3_ready": web3_ready,
        "admin_wallet": admin_address,
        "admin_eth_balance": admin_bal,
        "contracts": PRODUCTION_CONTRACTS,
        "gasless_enabled": gasless_ready
    }

@app.post("/api/engine/withdraw")
def withdraw_endpoint(request: WithdrawRequest):
    if not web3_ready:
        raise HTTPException(503, "Backend not connected")
    
    try:
        result = process_withdrawal(
            request.walletAddress,
            request.amount,
            request.tokenSymbol,
            request.tokenAddress
        )
        logger.info("Withdrawal success: %s", result["txHash"])
        return result
    except Exception as e:
        logger.error("Withdrawal error: %s", e)
        raise HTTPException(500, str(e))

@app.post("/api/engine/start")
def start_engine(data: dict):
    user_wallet = data.get("walletAddress", "").lower()
    logger.info("Engine started for: %s", user_wallet)
    return {"success": True}

@app.get("/api/engine/metrics")
def get_metrics():
    return {
        "hourlyRate": 45000.0,
        "dailyProfit": 1080000.0,
        "activePositions": 32
    }

@app.post("/api/engine/stop")
def stop_engine(data: dict):
    return {"success": True}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
