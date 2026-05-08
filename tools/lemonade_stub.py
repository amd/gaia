#!/usr/bin/env python3
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class StubHandler(BaseHTTPRequestHandler):
    def _set_json(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b''
        try:
            data = json.loads(body.decode('utf-8') or '{}')
        except Exception:
            data = {}
        if self.path.endswith('/api/v1/embeddings'):
            inputs = data.get('input') or data.get('inputs') or []
            if isinstance(inputs, str):
                inputs = [inputs]
            out = {'data': []}
            for i, txt in enumerate(inputs):
                emb = [((ord(c) if c else 0) % 100)/100.0 for c in (txt[:8] if txt else '')]
                emb = emb + [0.0]*(8 - len(emb))
                out['data'].append({'embedding': emb, 'index': i})
            out['model'] = 'lemonade-stub'
            self._set_json()
            self.wfile.write(json.dumps(out).encode('utf-8'))
            return
        elif self.path.endswith('/api/v1/completions') or self.path.endswith('/api/v1/chat/completions'):
            resp = {'id':'stub','object':'chat.completion','choices':[{'text':'','message': {'role':'assistant','content':'stub response'}}]}
            self._set_json()
            self.wfile.write(json.dumps(resp).encode('utf-8'))
            return
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == '__main__':
    server = HTTPServer(('localhost', 13305), StubHandler)
    print('Leomonade stub server running on http://localhost:13305')
    server.serve_forever()
