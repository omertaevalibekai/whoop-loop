"""Check that the wheel works outside the source tree, without API credentials."""
from pathlib import Path
import os
import subprocess
import tempfile
import venv

root = Path(__file__).resolve().parents[1]
wheels = list((root / "dist").glob("*.whl"))
assert len(wheels) == 1, "Build exactly one wheel first"
with tempfile.TemporaryDirectory() as temporary:
    cwd = Path(temporary)
    environment = cwd / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", str(wheels[0])], check=True, cwd=cwd)
    env = dict(os.environ, WHOOP_LOOP_HOME=str(cwd / "data"), PYTHONUTF8="1")
    code = "from whoop_loop.cli import main; main()"
    for args in [["--help"], ["demo", "--days", "120", "--with-illness"], ["digest"]]:
        subprocess.run([str(python), "-c", code, *args], check=True, cwd=cwd, env=env)
    subprocess.run([str(python), "-c", "import whoop_loop.mcp_server"], check=True, cwd=cwd, env=env)
print("Wheel CLI, demo, digest and MCP import passed outside the checkout")
