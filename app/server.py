"""开发/部署入口：标准库线程 HTTP 服务器承载 WSGI 应用。

环境变量：
- HOST       监听地址，默认 0.0.0.0
- PORT       监听端口，默认 8000
- DB_PATH    SQLite 文件路径，默认 /data/cutting.db
"""
from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import App
from database import Database


class WSGIHandler(BaseHTTPRequestHandler):
    server_version = "CuttingPlanner/1.0"

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def _handle(self):
        environ = {
            "REQUEST_METHOD": self.command,
            "PATH_INFO": self.path.split("?", 1)[0],
            "QUERY_STRING": self.path.split("?", 1)[1]
                            if "?" in self.path else "",
            "SERVER_NAME": self.server.server_address[0],
            "SERVER_PORT": str(self.server.server_address[1]),
            "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "wsgi.input": self.rfile,
            "wsgi.errors": self.server.wsgierrors,
        }
        for key, val in self.headers.items():

            environ[f"HTTP_{key.upper().replace('-', '_')}"] = val

        def start_response(status, headers):
            self.send_response(int(status.split(" ", 1)[0]),
                               status.split(" ", 1)[1] if " " in status else "")
            for hk, hv in headers:
                self.send_header(hk, hv)
            self.end_headers()

        for chunk in self.server.wsgi_app(environ, start_response):
            if chunk:
                self.wfile.write(chunk)

    def log_message(self, fmt, *args):  # 简洁访问日志
        import sys
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    db_path = os.environ.get("DB_PATH", "/data/cutting.db")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    db = Database(db_path)
    application = App(db)

    httpd = ThreadingHTTPServer((host, port), WSGIHandler)
    httpd.wsgi_app = application
    import sys
    httpd.wsgierrors = sys.stderr
    print(f"矩形纸张裁切排样服务已启动：http://{host}:{port}（DB={db_path}）",
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
