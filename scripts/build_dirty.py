"""Build benchmark and validate; invoke from project root with bundled NODE_BINARY."""
import os
import subprocess
import sys
from pathlib import Path
from prepare_dirty import build

project=Path(sys.argv[1] if len(sys.argv)>1 else '.').resolve()
output=Path(sys.argv[2] if len(sys.argv)>2 else project).resolve()
scripts=Path(__file__).parent
build(project,output)
node=os.environ.get('NODE_BINARY','node')
subprocess.run([node,str(scripts/'export_dirty.mjs'),str(output)],check=True)
subprocess.run([sys.executable,str(scripts/'validate_dirty.py'),str(project),str(output)],check=True)
(output/'matrices.json').unlink()
