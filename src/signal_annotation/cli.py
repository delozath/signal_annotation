"""Composition root. Only loopback binding is available."""
import argparse
from pathlib import Path
from bokeh.application import Application
from bokeh.application.handlers.function import FunctionHandler
from bokeh.server.server import Server
from .adapters.readers import BioreadReader, demo_recording
from .adapters.storage import JsonSidecarRepository, sidecar_path
from .adapters.bokeh_ui import build_document
from .application import AnnotationService
from .domain import ValidationError


def parser():
    p = argparse.ArgumentParser(description="Local ACQ viewer/annotator. Research prototype, not a medical device.")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Local .acq file, up to 20 MiB")
    source.add_argument("--demo", action="store_true", help="Six synthetic channels, no patient data")
    p.add_argument("--demo-dir", type=Path, default=Path.cwd(), help="Synthetic JSON folder; only for --demo")
    p.add_argument("--author", default="local", help="Annotator pseudonym; do not use patient data")
    p.add_argument("--port", type=int, default=5006)
    p.add_argument("--no-browser", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser().error("The port must be between 1024 and 65535.")
    try:
        recording = demo_recording() if args.demo else BioreadReader().read(args.file)
        if args.demo:
            folder = args.demo_dir.expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            sidecar = folder / "demo.annotations.json"
        else:
            sidecar = sidecar_path(args.file)
        repo = JsonSidecarRepository(sidecar, recording)
        # Validate stored workspace at startup before opening the browser.
        AnnotationService(recording, repo, args.author)
    except (ValidationError, OSError) as exc:
        parser().error(str(exc))

    def make_doc(doc):
        build_document(doc, AnnotationService(recording, JsonSidecarRepository(sidecar, recording), args.author), sidecar)

    server = Server({"/": Application(FunctionHandler(make_doc))}, address="127.0.0.1",
                    port=args.port, allow_websocket_origin=[f"127.0.0.1:{args.port}", f"localhost:{args.port}"],
                    num_procs=1)
    server.start()
    print(f"Local viewer: http://127.0.0.1:{args.port}/", flush=True)
    print(f"JSON: {sidecar} | Author: {args.author} | Ctrl+C to stop", flush=True)
    if not args.no_browser:
        server.io_loop.add_callback(server.show, "/")
    try:
        server.io_loop.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        server.io_loop.stop()


if __name__ == "__main__":
    main()
