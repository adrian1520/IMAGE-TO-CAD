from self_engine import EngineConfig, SelfEngine

engine = SelfEngine(EngineConfig(runtime_dir="/mnt/data/runtime", debug=True))
print(engine.run(image="/mnt/data/photo.jpg", paper="A3", dpi=1200, output=["png", "svg", "pdf", "docx", "dxf"]))
