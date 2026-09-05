#!/usr/bin/env python3
"""
Autonomous Freelance Agent - Full Stack
⚠️ WARNING: Grants unrestricted database and production deployment access
"""

import asyncio
import os
import json
import hashlib
import aiohttp
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from pyrogram import Client, filters
from pyrogram.types import Message
import subprocess
import docker
import asyncpg
import motor.motor_asyncio
from openai import AsyncOpenAI

# ============================================================================
# CONFIGURATION
# ============================================================================

class Config:
    # Telegram
    TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", ""))
    TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
    TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION", "agent_session")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    
    # LLM (Local or API)
    LLM_MODEL = os.getenv("LLM_MODEL", "qwen-2.5-72b")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
    
    # Database Access (Client DBs - stored encrypted)
    DB_CREDENTIALS = json.loads(os.getenv("DB_CREDENTIALS", "{}"))
    
    # Production Deployment
    PROD_SERVERS = json.loads(os.getenv("PROD_SERVERS", "{}"))
    CLOUDFLARE_API_KEY = os.getenv("CLOUDFLARE_API_KEY", "")
    CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    
    # Payment
    TELEGRAM_STARS_ENABLED = True
    USDC_CONTRACT = os.getenv("USDC_CONTRACT", "0x...")  # Claw Earn contract
    
    # Security (minimal - as requested)
    SANDBOX_ENABLED = False  # Disabled per your request
    MAX_EXECUTION_TIME = 300  # 5 minutes
    ALLOWED_EGRESS = ["*"]  # All destinations allowed
    
    # Channels to monitor (271+ from Taligram)
    MONITORED_CHANNELS = [
        "freelance_ir", "job_freelance", "remote_jobs_ir",
        "python_jobs", "devops_jobs", "security_jobs",
        # Add more from https://taligram.org/telegram-channels/jobs
    ]

config = Config()

# ============================================================================
# DISCOVERY LAYER - Telegram Channel Monitor
# ============================================================================

class DiscoveryAgent:
    def __init__(self, client: Client):
        self.client = client
        self.seen_messages = set()
        self.llm = AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    
    async def score_job(self, text: str) -> tuple[float, str]:
        """Score job relevance and extract key info"""
        prompt = f"""
        Analyze this freelance job posting. Return JSON:
        {{
            "score": 0-10 (relevance to: Python, security, Cloudflare, DevOps, network),
            "category": "script"|"security"|"cloudflare"|"devops"|"other",
            "budget": "extract if mentioned",
            "deadline": "extract if mentioned",
            "client_trust": "low"|"medium"|"high" (based on channel reputation)
        }}
        
        Job posting:
        {text[:2000]}
        """
        
        response = await self.llm.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        
        data = json.loads(response.choices[0].message.content)
        return data["score"], data["category"]
    
    async def process_message(self, message: Message):
        """Process incoming job posting"""
        msg_id = f"{message.chat.id}:{message.id}"
        if msg_id in self.seen_messages:
            return
        
        self.seen_messages.add(msg_id)
        
        if len(self.seen_messages) > 10000:
            self.seen_messages = set(list(self.seen_messages)[-5000:])
        
        text = message.text or message.caption or ""
        if len(text) < 50:
            return
        
        score, category = await self.score_job(text)
        
        if score >= 7.0:  # High relevance threshold
            print(f"[JOB FOUND] Score: {score}, Category: {category}")
            print(f"Channel: {message.chat.username or message.chat.id}")
            print(f"Text: {text[:200]}...")
            
            # Send to proposal engine
            await proposal_engine.submit_job(
                text=text,
                score=score,
                category=category,
                channel=message.chat.username or str(message.chat.id),
                message_id=msg_id
            )

# ============================================================================
# PROPOSAL ENGINE - Auto-generate and send proposals
# ============================================================================

class ProposalEngine:
    def __init__(self, llm: AsyncOpenAI):
        self.llm = llm
        self.proposal_queue = asyncio.Queue()
    
    async def submit_job(self, text: str, score: float, category: str, channel: str, message_id: str):
        await self.proposal_queue.put({
            "text": text,
            "score": score,
            "category": category,
            "channel": channel,
            "message_id": message_id,
            "timestamp": datetime.now().isoformat()
        })
    
    async def generate_proposal(self, job: dict) -> str:
        """Generate personalized proposal"""
        prompt = f"""
        Write a freelance proposal for this job. Be professional but direct.
        Include:
        - Brief intro (1 sentence)
        - How you'll solve their problem (2-3 sentences)
        - Price estimate (based on category)
        - Timeline (24-48 hours for small jobs)
        - Call to action
        
        Job:
        {job['text'][:1500]}
        
        Category: {job['category']}
        Score: {job['score']}
        """
        
        response = await self.llm.chat.completions.create(
            model=config.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.choices[0].message.content
    
    async def send_proposal(self, job: dict, proposal: str, client: Client):
        """Send proposal via Telegram DM or channel reply"""
        try:
            # Try to find client username in job text
            import re
            usernames = re.findall(r'@(\w+)', job['text'])
            
            if usernames:
                username = usernames[0]
                await client.send_message(username, proposal)
                print(f"[PROPOSAL SENT] to @{username}")
            else:
                # Reply in channel
                chat_id = int(job['channel']) if job['channel'].isdigit() else job['channel']
                await client.send_message(chat_id, f"📩 Proposal:\n\n{proposal}")
                print(f"[PROPOSAL SENT] to channel {job['channel']}")
        except Exception as e:
            print(f"[PROPOSAL FAILED] {e}")
    
    async def run(self, client: Client):
        """Main proposal loop"""
        while True:
            try:
                job = await asyncio.wait_for(self.proposal_queue.get(), timeout=1.0)
                proposal = await self.generate_proposal(job)
                await self.send_proposal(job, proposal, client)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"[PROPOSAL ERROR] {e}")

proposal_engine = ProposalEngine(
    AsyncOpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
)

# ============================================================================
# EXECUTION ENGINE - Run client work (with DB and Prod access)
# ============================================================================

class ExecutionAgent:
    def __init__(self):
        self.docker_client = docker.from_env()
        self.db_connections = {}
    
    async def connect_to_client_db(self, db_id: str) -> Any:
        """Connect to client database (PostgreSQL, MySQL, MongoDB)"""
        if db_id in self.db_connections:
            return self.db_connections[db_id]
        
        creds = config.DB_CREDENTIALS.get(db_id, {})
        
        if creds.get("type") == "postgresql":
            conn = await asyncpg.connect(
                host=creds["host"],
                port=creds["port"],
                user=creds["user"],
                password=creds["password"],
                database=creds["database"]
            )
            self.db_connections[db_id] = conn
            return conn
        
        elif creds.get("type") == "mysql":
            import aiomysql
            conn = await aiomysql.connect(
                host=creds["host"],
                port=creds["port"],
                user=creds["user"],
                password=creds["password"],
                db=creds["database"]
            )
            self.db_connections[db_id] = conn
            return conn
        
        elif creds.get("type") == "mongodb":
            client = motor.motor_asyncio.AsyncIOMotorClient(creds["uri"])
            self.db_connections[db_id] = client
            return client
        
        raise ValueError(f"Unknown DB type: {creds.get('type')}")
    
    async def execute_code(self, code: str, language: str = "python") -> str:
        """Execute client code in sandbox (or directly if disabled)"""
        if config.SANDBOX_ENABLED:
            # Run in Docker container
            container = self.docker_client.containers.run(
                "python:3.11-slim",
                command=f"python -c \"{code}\"",
                remove=True,
                network_mode="none" if "*" not in config.ALLOWED_EGRESS else "bridge",
                mem_limit="512m",
                cpu_quota=50000,
                detach=False
            )
            return container.decode()
        else:
            # Direct execution (as requested - no sandbox)
            result = subprocess.run(
                ["python3", "-c", code],
                capture_output=True,
                text=True,
                timeout=config.MAX_EXECUTION_TIME
            )
            return result.stdout or result.stderr
    
    async def deploy_to_production(self, target: str, artifact: str) -> str:
        """Deploy to production server (VPS, Cloudflare, Docker Hub)"""
        server = config.PROD_SERVERS.get(target, {})
        
        if server.get("type") == "ssh":
            # SSH deployment
            import asyncssh
            async with asyncssh.connect(
                host=server["host"],
                port=server.get("port", 22),
                username=server["user"],
                password=server.get("password"),
                private_key=server.get("key_file")
            ) as conn:
                result = await conn.run(f"echo '{artifact}' | bash")
                return await result.read()
        
        elif server.get("type") == "cloudflare":
            # Cloudflare Worker deployment
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {config.CLOUDFLARE_API_KEY}",
                    "Content-Type": "application/javascript"
                }
                url = f"https://api.cloudflare.com/client/v4/accounts/{config.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/{target}"
                
                async with session.put(url, data=artifact, headers=headers) as resp:
                    return await resp.text()
        
        elif server.get("type") == "docker":
            # Docker Hub push
            self.docker_client.images.build(
                path=artifact,
                tag=f"{server['repo']}:latest"
            )
            self.docker_client.images.push(f"{server['repo']}:latest")
            return f"Deployed to {server['repo']}"
        
        raise ValueError(f"Unknown server type: {server.get('type')}")

execution_agent = ExecutionAgent()

# ============================================================================
# PAYMENT ENGINE - Telegram Stars + USDC Escrow
# ============================================================================

class PaymentEngine:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.base_url = "https://api.telegram.org/bot" + bot_token
    
    async def create_stars_invoice(self, amount: int, description: str, user_id: int) -> str:
        """Create Telegram Stars invoice"""
        async with aiohttp.ClientSession() as session:
            payload = {
                "chat_id": user_id,
                "title": "Freelance Service",
                "description": description,
                "payload": hashlib.sha256(f"{user_id}{datetime.now()}".encode()).hexdigest(),
                "provider_token": "",  # Not needed for Stars
                "currency": "XTR",
                "prices": [{"label": "Service", "amount": amount}],
                "is_flexible": False
            }
            
            async with session.post(
                f"{self.base_url}/sendInvoice",
                json=payload
            ) as resp:
                data = await resp.json()
                return data["result"]["invoice_id"] if data.get("ok") else None
    
    async def create_usdc_escrow(self, amount: float, client_address: str, job_id: str) -> str:
        """Create USDC escrow via Claw Earn"""
        # Claw Earn API (simplified)
        async with aiohttp.ClientSession() as session:
            payload = {
                "amount": amount,
                "token": "USDC",
                "client": client_address,
                "agent": "0xYOUR_AGENT_ADDRESS",
                "job_id": job_id,
                "auto_approve_hours": 48
            }
            
            async with session.post(
                "https://api.clawearn.xyz/escrow/create",
                json=payload
            ) as resp:
                data = await resp.json()
                return data["escrow_address"]

payment_engine = PaymentEngine(config.TELEGRAM_BOT_TOKEN)

# ============================================================================
# MAIN AGENT LOOP
# ============================================================================

async def main():
    # Initialize Telegram client
    app = Client(
        config.TELEGRAM_SESSION,
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH
    )
    
    discovery = DiscoveryAgent(app)
    
    # Set up message handlers
    @app.on_message(filters.channel)
    async def on_channel_message(client: Client, message: Message):
        await discovery.process_message(message)
    
    @app.on_message(filters.private)
    async def on_private_message(client: Client, message: Message):
        # Handle client responses
        text = message.text or ""
        if "I accept" in text.lower() or "approved" in text.lower():
            # Job accepted - execute work
            print(f"[JOB ACCEPTED] by {message.from_user.username}")
            # TODO: Trigger execution_agent with job details
    
    # Start proposal engine
    proposal_task = asyncio.create_task(proposal_engine.run(app))
    
    # Start monitoring
    print("[AGENT STARTED] Monitoring channels...")
    await app.run()

if __name__ == "__main__":
    asyncio.run(main())
