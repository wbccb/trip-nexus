import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return


def main():
    port = int(os.getenv("PORT", "10000"))
    host = "0.0.0.0"
    print(f"render_port_probe listening on {host}:{port}", flush=True)
    server = HTTPServer((host, port), _Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
