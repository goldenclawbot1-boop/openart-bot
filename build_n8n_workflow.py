"""
n8n Workflow Builder for Jewelry AI Generation
Creates a workflow that:
1. Receives webhook data (image_url, prompt, output_dir)
2. Calls fal.ai GPT Image 2 API
3. Downloads result to local folder
4. Returns output filename
"""

import asyncio
import aiohttp
import json
import requests
from pathlib import Path

N8N_API_URL = "http://localhost:5678"
N8N_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI4OWFiNzAzNy05MmQwLTRmN2QtOWVlMi1kNGZiMDA2NDhkNmYiLCJpc3MiOiJuOG4iLCJhdWQiOiS1dWJsaWMtYXBpIiwianRpIjoiOTJjY2Q2MjAtYzYyZS00ZGU0LWJlNWEtOTE4NjdmNDkzNjQ1IiwiaWF0IjoxNzgxNDg5ODI2fQ._09GSp_a4NP5rpH70p2RsxotId-3tFqhYaXUMt80HUc"

# n8n Workflow Structure
workflow_data = {
    "name": "Jewelry AI Generation",
    "nodes": [
        {
            "parameters": {
                "path": "jewelry-generation",
                "httpMethod": "POST",
                "responseMode": "lastNode",
                "options": {}
            },
            "name": "Webhook",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 1,
            "position": [250, 300]
        },
        {
            "parameters": {
                "url": "https://fal.run/openai/gpt-image-2/edit",
                "authentication": "genericCredentialType",
                "options": {
                    "headerName1": "Authorization",
                    "headerValue1": "Key aba0afab-6bce-403a-8929-e78f08b6ace8:8b2740446eddcda5b41684dae7e11d1b"
                }
            },
            "name": "HTTP Request",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 1,
            "position": [500, 300],
            "webhookId": "jewelry-generation"
        },
        {
            "parameters": {
                "options": {}
            },
            "name": "Edit Image",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 1,
            "position": [700, 300]
        },
        {
            "parameters": {
                "options": {
                    "jsonParameters": True,
                    "outputPropertyName": ""
                }
            },
            "name": "Set Output",
            "type": "n8n-nodes-base.set",
            "typeVersion": 1,
            "position": [900, 300]
        }
    ],
    "connections": {
        "Webhook": {
            "main": [
                {
                    "node": "HTTP Request",
                    "type": "main",
                    "index": 0
                }
            ]
        },
        "HTTP Request": {
            "main": [
                {
                    "node": "Set Output",
                    "type": "main",
                    "index": 0
                }
            ]
        }
    },
    "settings": {
        "executionOrder": "v1"
    }
}

async def check_n8n_status():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{N8N_API_URL}/health") as resp:
                return resp.status == 200
    except:
        return False

async def create_workflow():
    """Create or update the jewelry workflow in n8n"""
    print("Checking n8n status...")
    if not await check_n8n_status():
        print("n8n is not running!")
        return None
    
    # Check if workflow already exists
    async with aiohttp.ClientSession() as session:
        # Get workflows list
        async with session.get(
            f"{N8N_API_URL}/api/v1/workflows",
            headers={"X-N8N-API-KEY": N8N_API_KEY}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                for workflow in data.get("data", []):
                    if workflow.get("name") == "Jewelry AI Generation":
                        print(f"Found existing workflow: {workflow['id']}")
                        # Update existing workflow
                        async with session.patch(
                            f"{N8N_API_URL}/api/v1/workflows/{workflow['id']}",
                            headers={"X-N8N-API-KEY": N8N_API_KEY, "Content-Type": "application/json"},
                            json=workflow_data
                        ) as update_resp:
                            result = await update_resp.json()
                            print(f"Updated workflow: {result}")
                            return result
    
    # Create new workflow if not found
    print("Creating new workflow...")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{N8N_API_URL}/api/v1/workflows",
            headers={"X-N8N-API-KEY": N8N_API_KEY, "Content-Type": "application/json"},
            json=workflow_data
        ) as resp:
            result = await resp.json()
            print(f"Created workflow: {result}")
            return result

async def activate_workflow(workflow_id):
    """Activate the workflow"""
    print(f"Activating workflow {workflow_id}...")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{N8N_API_URL}/api/v1/workflows/{workflow_id}/activate",
            headers={"X-N8N-API-KEY": N8N_API_KEY}
        ) as resp:
            result = await resp.json()
            print(f"Workflow activated: {result}")
            return result

async def main():
    print("Starting n8n workflow setup...")
    
    # Create/update workflow
    result = await create_workflow()
    if not result:
        print("Failed to create workflow!")
        return
    
    workflow_id = result.get("id")
    if not workflow_id:
        # Try to get from response structure
        if isinstance(result, dict):
            workflow_id = result.get("id") or result.get("workflowId")
    
    if workflow_id:
        # Activate workflow
        await activate_workflow(workflow_id)
        print(f"\n✅ Workflow setup complete!")
        print(f"Webhook URL: http://localhost:5678/webhook/jewelry-generation")
    else:
        print(f"Could not determine workflow ID from: {result}")

if __name__ == "__main__":
    asyncio.run(main())
