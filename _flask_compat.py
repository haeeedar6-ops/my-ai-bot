# -*- coding: utf-8 -*-
"""
_flask_compat.py — طبقة توافق خفيفة لبيئات الاختبار المعزولة التي لا تتوفر فيها مكتبة flask.
في بيئة الإنتاج (Render)، يتم استيراد مكتبة flask الحقيقية مباشرة ولا تُستخدم هذه الطبقة.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse


class _RequestProxy:
    def __init__(self):
        self.reset()

    def reset(self, method="GET", path="/", headers=None, json_data=None, data=b"", args=None, files=None, host="localhost"):
        self.method = method.upper()
        self.path = path
        self.headers = dict(headers or {})
        self._json = json_data
        self.data = data if isinstance(data, bytes) else str(data or "").encode("utf-8")
        self.args = dict(args or {})
        self.files = dict(files or {})
        self.host = host
        self.remote_addr = "127.0.0.1"
        self.cookies = {}

    def get_json(self, silent=False, force=False):
        if self._json is not None:
            return self._json
        if self.data:
            try:
                return json.loads(self.data.decode("utf-8"))
            except Exception:
                if not silent:
                    raise
        return None


class _SessionProxy(dict):
    def __init__(self):
        super().__init__()
        self.permanent = False


request = _RequestProxy()
session = _SessionProxy()


class Response:
    def __init__(self, response=b"", status=200, headers=None, mimetype="text/html", content_type=None):
        if isinstance(response, str):
            self.data = response.encode("utf-8")
        elif isinstance(response, bytes):
            self.data = response
        elif isinstance(response, dict):
            self.data = json.dumps(response, ensure_ascii=False).encode("utf-8")
            mimetype = "application/json"
        else:
            self.data = b"".join(
                x.encode("utf-8") if isinstance(x, str) else bytes(x) for x in (response or [])
            )
        self.status_code = int(status)
        self.headers = dict(headers or {})
        self.mimetype = content_type or mimetype

    def get_data(self, as_text=False):
        return self.data.decode("utf-8", errors="replace") if as_text else self.data

    def get_json(self, silent=False):
        try:
            return json.loads(self.data.decode("utf-8"))
        except Exception:
            if not silent:
                raise
            return None


def jsonify(*args, **kwargs):
    if args and kwargs:
        raise TypeError("jsonify() behavior undefined when passed both args and kwargs")
    if len(args) == 1:
        payload = args[0]
    elif args:
        payload = list(args)
    else:
        payload = kwargs
    return Response(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        status=200,
        mimetype="application/json",
    )


class _TestClient:
    def __init__(self, app: "Flask"):
        self.app = app
        self._session_store = {}

    def _req(self, method: str, url: str, json=None, data=None, headers=None):
        parsed = urlparse(url)
        path = parsed.path or "/"
        q_args = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}
        hdrs = {}
        for k, v in (headers or {}).items():
            hdrs[k] = v
        # Normalize case-insensitive header lookup
        class _CIHeaders(dict):
            def get(self, key, default=None):
                for hk, hv in self.items():
                    if hk.lower() == str(key).lower():
                        return hv
                return default
        ci_headers = _CIHeaders(hdrs)

        session.clear()
        session.update(self._session_store)
        raw_bytes = b""
        if json is not None:
            import json as _json
            raw_bytes = _json.dumps(json, ensure_ascii=False).encode("utf-8")
        elif isinstance(data, bytes):
            raw_bytes = data
        elif isinstance(data, str):
            raw_bytes = data.encode("utf-8")

        request.reset(
            method=method,
            path=path,
            headers=ci_headers,
            json_data=json,
            data=raw_bytes,
            args=q_args,
        )
        resp = self.app._dispatch(method.upper(), path)
        self._session_store = dict(session)
        return resp

    def get(self, url, **kw):
        return self._req("GET", url, **kw)

    def post(self, url, **kw):
        return self._req("POST", url, **kw)

    def put(self, url, **kw):
        return self._req("PUT", url, **kw)

    def delete(self, url, **kw):
        return self._req("DELETE", url, **kw)


class Flask:
    def __init__(self, import_name: str):
        self.import_name = import_name
        self.secret_key = ""
        self.config = {}
        self._routes = []
        self._after_request_funcs = []
        self._before_request_funcs = []

    def after_request(self, fn):
        self._after_request_funcs.append(fn)
        return fn

    def before_request(self, fn):
        self._before_request_funcs.append(fn)
        return fn

    def errorhandler(self, _code):
        def decorator(fn):
            return fn
        return decorator

    def _add_route(self, rule: str, methods: list[str], func):
        param_names = []
        def _repl(m):
            name = m.group(1).split(":")[-1]
            param_names.append(name)
            return f"(?P<{name}>[^/]+)"
        regex_pat = "^" + re.sub(r"<([^>]+)>", _repl, rule) + "$"
        self._routes.append((re.compile(regex_pat), [m.upper() for m in methods], func))

    def route(self, rule: str, methods=None, **_kw):
        ms = list(methods or ["GET"])
        def decorator(fn):
            self._add_route(rule, ms, fn)
            return fn
        return decorator

    def get(self, rule: str, **_kw):
        return self.route(rule, methods=["GET"])

    def post(self, rule: str, **_kw):
        return self.route(rule, methods=["POST"])

    def put(self, rule: str, **_kw):
        return self.route(rule, methods=["PUT"])

    def delete(self, rule: str, **_kw):
        return self.route(rule, methods=["DELETE"])

    def _dispatch(self, method: str, path: str) -> Response:
        for bfn in self._before_request_funcs:
            brv = bfn()
            if brv is not None:
                resp = self._make_response(brv)
                for afn in self._after_request_funcs:
                    resp = afn(resp) or resp
                return resp
        for pat, methods, fn in self._routes:
            m = pat.match(path)
            if m and method in methods:
                rv = fn(**m.groupdict())
                resp = self._make_response(rv)
                for afn in self._after_request_funcs:
                    resp = afn(resp) or resp
                return resp
        resp = Response("Not Found", status=404)
        for afn in self._after_request_funcs:
            resp = afn(resp) or resp
        return resp

    @staticmethod
    def _make_response(rv) -> Response:
        status = 200
        headers = {}
        body = rv
        if isinstance(rv, tuple):
            if len(rv) == 2:
                body, status = rv
            elif len(rv) == 3:
                body, status, headers = rv
        if isinstance(body, Response):
            body.status_code = int(status)
            body.headers.update(headers or {})
            return body
        return Response(body, status=status, headers=headers)

    def test_client(self) -> _TestClient:
        return _TestClient(self)

    def run(self, host="127.0.0.1", port=5000, **_kw):
        pass
