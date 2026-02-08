

# python-poller/run_gpt_once.py
import sys, subprocess, os, pathlib, tempfile

USAGE = """usage:
  python run_gpt_once.py GRAPH.xml INPUT.SAFE|.zip OUTPUT.tif "POLYGON((...))"
  python run_gpt_once.py GRAPH.xml INPUT.SAFE|.zip OUTPUT.tif aoi.wkt
"""

def main(graph, inp, outp, wkt_arg):
    graph = str(pathlib.Path(graph).resolve())
    inp   = str(pathlib.Path(inp).resolve())
    outp  = str(pathlib.Path(outp).resolve())
    
    # Accept inline WKT string or a path to a .wkt/.txt file
    print(wkt_arg)
    wkt_text = wkt_arg.strip()

    gpt = os.environ.get("SNAP_GPT", r"C:\Program Files\esa-snap\bin\gpt.exe")

    args = [
        gpt, graph,
        "-e", "-c", "2G",
        "-J-Djava.awt.headless=true",
        f"-Pin={inp}",
        f"-Paoi_wkt={str(wkt_arg)}",
        f"-Pout={outp}",
    ]

    print("[gpt]", " ".join(args), flush=True)
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(proc.stdout)
    sys.exit(proc.returncode)

if __name__ == "__main__":
    # IMPORTANT: validate BEFORE indexing argv
    if len(sys.argv) < 5:
        print(USAGE, file=sys.stderr); #sys.exit(2)
    # Join the remaining args as WKT (protects against accidental splits)
    graph, inp, outp = sys.argv[1], sys.argv[2], sys.argv[3]
    wkt_arg = " ".join(sys.argv[4:])
    main(graph, inp, outp, str(wkt_arg))
