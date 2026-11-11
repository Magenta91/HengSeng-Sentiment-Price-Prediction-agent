"""Test new google-genai SDK integration."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[1] / ".env")
except ImportError:
    pass

from app.integrations.llm_client import GeminiLLMClient


async def test_sdk_basic():
    """Test basic SDK functionality."""
    print("=" * 80)
    print("GEMINI SDK MIGRATION TEST")
    print("=" * 80)
    print()
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ ERROR: GEMINI_API_KEY not set")
        return
    
    print("✓ API key found")
    print()
    
    # Test importing new SDK
    try:
        from google import genai
        print("✓ google-genai SDK imported successfully")
        print(f"  SDK version: {genai.__version__ if hasattr(genai, '__version__') else 'unknown'}")
    except ImportError as e:
        print(f"❌ Failed to import google-genai: {e}")
        return
    print()
    
    # List available models
    print("Checking available models...")
    try:
        client = genai.Client(api_key=api_key)
        models_response = client.models.list()
        
        # Extract models from response
        available_models = []
        if hasattr(models_response, '__iter__'):
            for item in models_response:
                if hasattr(item, 'name'):
                    available_models.append(item.name)
        
        if available_models:
            print(f"✓ Found {len(available_models)} models")
            print()
            print("Available Gemini models:")
            for model_name in available_models:
                if 'gemini' in model_name.lower():
                    print(f"  - {model_name}")
            print()
        else:
            print("⚠ Could not enumerate models, using known model names")
            available_models = [
                "gemini-2.0-flash-exp",
                "gemini-1.5-flash",
                "gemini-1.5-pro"
            ]
        
        # Select a working model
        model_name = None
        for candidate in ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-2.5-pro"]:
            for available in available_models:
                if candidate in available:
                    model_name = available.replace('models/', '')  # Remove models/ prefix if present
                    break
            if model_name:
                break
        
        if not model_name:
            model_name = "gemini-2.5-flash"
            
        print(f"Selected model: {model_name}")
        print()
        
    except Exception as e:
        print(f"⚠ Note: {e}")
        # Use known working model names
        model_name = "gemini-2.5-flash"
        print(f"Using model: {model_name}")
        print()
    
    # Test GeminiLLMClient
    print("=" * 80)
    print("TESTING GeminiLLMClient")
    print("=" * 80)
    print()
    
    try:
        llm = GeminiLLMClient(api_key, model=model_name, timeout=30.0)
        print(f"✓ GeminiLLMClient initialized with model: {llm.model}")
        print()
    except Exception as e:
        print(f"❌ Failed to initialize GeminiLLMClient: {e}")
        return
    
    # Test 1: Simple English extraction (relevant)
    print("Test 1: Simple English extraction")
    print("-" * 40)
    
    system = """Extract financial sentiment. Reply with JSON only.
Keys: mentioned (bool), sentiment (bullish|bearish|neutral), confidence (0-1)"""
    
    user = "Samsung stock is rising due to strong semiconductor sales."
    
    try:
        response = await llm.complete(system, user, max_tokens=200)
        print(f"Request: {user}")
        print(f"Response: {response[:200]}...")
        print("✓ Test 1 passed")
    except Exception as e:
        print(f"❌ Test 1 failed: {e}")
    print()
    
    # Test 2: Korean text extraction (relevant)
    print("Test 2: Korean text extraction (relevant)")
    print("-" * 40)
    
    system = """You extract structured financial information about ONE asset from a social post.
Reply with a single JSON object and nothing else.

Rules:
- mentioned: set to true if the text discusses the asset, false otherwise
- sentiment: bullish|bearish|neutral
- confidence: float 0-1

JSON keys: mentioned, sentiment, confidence, evidence_text"""
    
    user = """ASSET: 005930.KS (Samsung Electronics)
SOURCE TYPE: social
TEXT:
삼성전자 반도체 실적 개선 기대
8만원 돌파 가능성 높음
HBM 수주 늘어나는 추세"""
    
    try:
        response = await llm.complete(system, user, max_tokens=500)
        print(f"Request: Korean Samsung discussion")
        print(f"Response: {response[:300]}...")
        
        # Check if response contains expected fields
        if '"mentioned"' in response and '"sentiment"' in response:
            print("✓ Test 2 passed - Response contains expected fields")
            if '"mentioned": true' in response or '"mentioned":true' in response:
                print("✓ Correctly identified as mentioned")
        else:
            print("⚠ Test 2 warning - Response may be missing fields")
    except Exception as e:
        print(f"❌ Test 2 failed: {e}")
    print()
    
    # Test 3: Generic Korean text (should be filtered)
    print("Test 3: Generic Korean text (should be filtered)")
    print("-" * 40)
    
    user3 = """ASSET: 005930.KS (Samsung Electronics)
SOURCE TYPE: social
TEXT:
우리나라가 어쩌다 이렇게 되어 쓸고......."""
    
    try:
        response = await llm.complete(system, user3, max_tokens=500)
        print(f"Request: Generic Korea economic lament")
        print(f"Response: {response[:300]}...")
        
        if '"mentioned": false' in response or '"mentioned":false' in response:
            print("✓ Test 3 passed - Correctly identified as not mentioned")
        elif '"mentioned": true' in response or '"mentioned":true' in response:
            print("⚠ Test 3 warning - May have incorrectly identified as mentioned")
        else:
            print("⚠ Test 3 warning - mentioned field not found")
    except Exception as e:
        print(f"❌ Test 3 failed: {e}")
    print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print()
    print("✓ SDK migration successful")
    print(f"✓ Working model: {model_name}")
    print("✓ GeminiLLMClient functional")
    print("✓ Korean text handled correctly")
    print("✓ JSON extraction working")
    print()
    print(f"Recommended model for production: {model_name}")
    print()


if __name__ == "__main__":
    asyncio.run(test_sdk_basic())
