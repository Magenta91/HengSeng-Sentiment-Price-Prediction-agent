"""Create the DEMO model (synthetic data) in model_artifacts/demo/ :  python -m scripts.make_demo_artifacts"""
import asyncio

from app.demo.artifacts import build_demo_artifacts

if __name__ == "__main__":
    meta = asyncio.run(build_demo_artifacts("model_artifacts/demo"))
    print("wrote model_artifacts/demo/ (DEMO ONLY - synthetic prices):", meta["model_version"])
