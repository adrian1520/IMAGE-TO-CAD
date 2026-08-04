import time
from pathlib import Path
from self_engine import EngineConfig, SelfEngine


def benchmark(image: str, runtime_dir: str = "/tmp/self_engine_benchmark") -> None:
    start = time.time()
    result = SelfEngine(EngineConfig(runtime_dir=runtime_dir, output=("svg", "dxf"), debug=False)).run(image=image)
    print({"seconds": round(time.time() - start, 3), "artifacts": result["artifacts"]})


if __name__ == "__main__":
    sample = Path("/mnt/data/photo.jpg")
    if not sample.exists():
        raise SystemExit("Place a sample at /mnt/data/photo.jpg or import benchmark(image=...).")
    benchmark(str(sample))
