import http.server
import socketserver

PORT = 8899
class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'PORT_8899_SUCCESS')

with socketserver.TCPServer(('0.0.0.0', PORT), Handler) as httpd:
    print('SERVER_READY_8899', flush=True)
    httpd.serve_forever()