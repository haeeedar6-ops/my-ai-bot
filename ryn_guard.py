"""
ryn_guard.py - deterministic code guard for Ryn mix.

Stdlib only, no network. Drop it next to webapp.py.

What it gives you:
  scan_python(code)            -> list[Finding]   (AST + light taint tracking)
  diff_gate(old, new, ...)     -> GateResult      (contract / scope / gate-integrity / new deps)
  detect_injection(text)       -> list[str]       (prompt-injection lines inside user input)
  build_fix_prompt(code, fs)   -> str             (findings + ready secure patterns for the fixer)
  closed_loop(code, fix_fn)    -> dict            (scan -> fix -> rescan, up to N rounds)
  finalize_report(...)         -> str             (status computed from facts, not from the model)
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from dataclasses import dataclass

SEV = {"critical": 3, "high": 2, "medium": 1, "info": 0}


@dataclass
class Finding:
    rule: str
    sev: str
    line: int
    func: str
    msg: str

    def text(self) -> str:
        return "[%s] %s @ %s:%d - %s" % (self.sev.upper(), self.rule, self.func, self.line, self.msg)


# --------------------------------------------------------------------------
# Secure patterns handed to the fixer model (one per rule)
# --------------------------------------------------------------------------
REMEDIES = {
    "sqli": 'db.execute("SELECT pw FROM users WHERE name = ?", (u,))   # placeholders only, never f-strings / + / %',
    "cmd-injection": 'subprocess.run(["ping", "-c", "1", "--", host], capture_output=True, timeout=5)   # list args, no shell=True',
    "cmd-arg-injection": (
        'if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host): abort(400)\n'
        'subprocess.run(["ping", "-c", "1", "--", host], capture_output=True, timeout=5)   # "--" ends options'
    ),
    "unsafe-deserialization": "data = json.loads(request.data)   # never pickle/marshal/eval/yaml.load on user data",
    "hardcoded-secret": 'SECRET = os.environ["APP_SECRET_KEY"]   # fail fast, no literal and no default',
    "secret-default": 'SECRET = os.environ["APP_SECRET_KEY"]   # no fallback value for secrets',
    "weak-password-hash": (
        "def hash_pw(pw, salt=None):\n"
        "    salt = salt or os.urandom(16)\n"
        '    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 600_000)\n'
        '    return salt.hex() + "$" + dk.hex()\n'
        "def verify_pw(pw, stored):\n"
        '    salt_hex, dk_hex = stored.split("$", 1)\n'
        '    cand = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 600_000)\n'
        "    return hmac.compare_digest(cand.hex(), dk_hex)\n"
        "# old users need a password reset or a one-time rehash on login (ask the owner)"
    ),
    "static-salt": "salt = os.urandom(16) per password, stored next to the hash. A constant salt is not a salt.",
    "timing-compare": "hmac.compare_digest(a, b)   # never == on hashes or tokens",
    "open-redirect": (
        "def _safe_next(target):\n"
        '    if not target or "\\\\" in target: return "/"\n'
        "    p = urlparse(target)\n"
        '    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//"): return "/"\n'
        "    return target\n"
        '# redirect(_safe_next(request.args.get("next", "/")))'
    ),
    "ssrf": (
        "def _assert_public_url(url):\n"
        "    p = urlparse(url)\n"
        '    if p.scheme not in ("http", "https") or not p.hostname: raise ValueError("bad url")\n'
        "    for info in socket.getaddrinfo(p.hostname, p.port or 443):\n"
        "        ip = ipaddress.ip_address(info[4][0])\n"
        "        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:\n"
        '            raise ValueError("blocked address")\n'
        "_assert_public_url(url); requests.get(url, timeout=5, allow_redirects=False)"
    ),
    "ssrf-redirect": "requests.get(url, timeout=5, allow_redirects=False)   # a public URL can still redirect inside",
    "path-traversal": 'return send_from_directory(UPLOAD_DIR, request.args["name"])   # safe_join inside, no manual path math',
    "ssti-xss": 'render_template_string("<h1>{{ n }}</h1>", n=row[0])   # constant template, values as variables',
    "reflected-xss": "never build HTML by concatenation; use a constant template + variables (auto-escaped)",
    "markup-unsafe": "Markup() only on constants; wrap every dynamic value with escape() first",
    "no-auth-admin": (
        "def require_admin(fn):\n"
        "    @wraps(fn)\n"
        "    def wrapper(*a, **kw):\n"
        '        tok = request.headers.get("X-Admin-Token", "")\n'
        "        if not ADMIN_TOKEN or not hmac.compare_digest(tok, ADMIN_TOKEN): abort(403)\n"
        "        return fn(*a, **kw)\n"
        "    return wrapper\n"
        "# put @require_admin under @app.route; ADMIN_TOKEN comes from env"
    ),
    "unbacked-claim": "delete the comment, or really add the code it claims. A comment is not a fix.",
    "debug-true": "app.run(host=os.environ.get('HOST', '127.0.0.1'), debug=False)",
    "mutable-default": "def f(x, history=None):\n    history = [] if history is None else history",
    "swallowed-exception": "log the error and return a proper status; never `except: pass`",
}

# rules where the fix needs a decision from the owner (not just code)
DECISION_RULES = {"no-auth-admin", "weak-password-hash"}


# --------------------------------------------------------------------------
# AST helpers
# --------------------------------------------------------------------------
def _dotted(n) -> str:
    parts = []
    while isinstance(n, ast.Attribute):
        parts.append(n.attr)
        n = n.value
    if isinstance(n, ast.Name):
        parts.append(n.id)
    elif isinstance(n, ast.Call):
        parts.append(_dotted(n.func) + "()")
    else:
        parts.append("?")
    return ".".join(reversed(parts))


def _tail(n) -> str:
    return _dotted(n).split(".")[-1]


def _const_str(n) -> bool:
    return isinstance(n, ast.Constant) and isinstance(n.value, str)


def _is_dynamic_string(n) -> bool:
    if isinstance(n, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in n.values)
    if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Mod)):
        return not (_const_str(n.left) and _const_str(n.right))
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "format":
        return _const_str(n.func.value) or _is_dynamic_string(n.func.value)
    return False


def _shallow(root):
    """Walk a function/module body without entering nested defs/classes/lambdas."""
    stack = list(reversed(getattr(root, "body", [])))
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(n))))


_SANITIZERS = {"escape", "html_escape", "quote", "int", "float", "bool", "len", "secure_filename"}
_DB_SOURCES = {"fetchone", "fetchall", "fetchmany"}


def _tainted_expr(node, tainted) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "request" or node.id in tainted
    if isinstance(node, ast.Call):
        t = _tail(node.func)
        if t in _SANITIZERS:
            return False
        if t in _DB_SOURCES:
            return True
    for ch in ast.iter_child_nodes(node):
        if _tainted_expr(ch, tainted):
            return True
    return False


def _routes(fn):
    out = []
    for d in getattr(fn, "decorator_list", []):
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in (
            "route", "get", "post", "put", "delete", "patch"
        ):
            path = d.args[0].value if d.args and _const_str(d.args[0]) else None
            methods = None
            for k in d.keywords:
                if k.arg == "methods" and isinstance(k.value, (ast.List, ast.Tuple)):
                    methods = tuple(sorted(e.value for e in k.value.elts if isinstance(e, ast.Constant)))
            if methods is None:
                methods = ("GET",) if d.func.attr == "route" else (d.func.attr.upper(),)
            out.append((path, methods))
    return out


def _taint_set(fn) -> set:
    t = set()
    for r in _routes(fn):
        for nm in re.findall(r"<(?:\w+:)?(\w+)>", r[0] or ""):
            t.add(nm)
    for _ in range(6):
        grew = False
        for n in _shallow(fn):
            if isinstance(n, ast.Assign):
                targets, val = n.targets, n.value
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and n.value is not None:
                targets, val = [n.target], n.value
            elif isinstance(n, ast.For):
                targets, val = [n.target], n.iter
            else:
                continue
            if _tainted_expr(val, t):
                for tg in targets:
                    for nm in ast.walk(tg):
                        if isinstance(nm, ast.Name) and isinstance(nm.ctx, ast.Store) and nm.id not in t:
                            t.add(nm.id)
                            grew = True
        if not grew:
            break
    return t


def _with_helpers(fn, funcs, depth=2):
    seen, frontier = [fn], [fn]
    for _ in range(depth):
        nxt = []
        for f in frontier:
            for n in ast.walk(f):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                    g = funcs.get(n.func.id)
                    if g is not None and g not in seen:
                        seen.append(g)
                        nxt.append(g)
        frontier = nxt
    return seen


class _Feat:
    def __init__(self, nodes):
        self.attrs, self.names, self.consts, self.calls = set(), set(), set(), set()
        for root in nodes:
            for n in ast.walk(root):
                if isinstance(n, ast.Attribute):
                    self.attrs.add(n.attr)
                elif isinstance(n, ast.Name):
                    self.names.add(n.id)
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    self.consts.add(n.value)
                elif isinstance(n, ast.Call):
                    self.calls.add(_tail(n.func))


def _arg0(call):
    if call.args:
        return call.args[0]
    for k in call.keywords:
        if k.arg in ("url", "path", "file", "filename", "args"):
            return k.value
    return None


def _kw(call, name):
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


# --------------------------------------------------------------------------
# Scanner
# --------------------------------------------------------------------------
_SECRET_NAME = re.compile(r"(secret|token|api[_-]?key|passwd|password|private[_-]?key)", re.I)
_SECRET_VALUE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|AIza[0-9A-Za-z_\-]{30,}|xox[baprs]-[A-Za-z0-9\-]{10,})"
)
_PLACEHOLDER = re.compile(r"(changeme|example|your[_-]|xxx|placeholder|<.*>|\*{3,})", re.I)
_PW_FUNC = re.compile(r"(pw|pass|passwd|password|login|auth|credential)", re.I)
_HASH_CALLS = {"hashlib.md5", "hashlib.sha1", "hashlib.sha224", "hashlib.sha256", "hashlib.sha384",
               "hashlib.sha512", "hashlib.new", "md5", "sha1", "sha256"}
_STRONG_KDF = {"pbkdf2_hmac", "scrypt", "bcrypt", "hashpw", "argon2", "generate_password_hash", "PasswordHasher"}
_HTTP_CALLS = {"requests.get", "requests.post", "requests.put", "requests.delete", "requests.head",
               "requests.patch", "requests.request", "httpx.get", "httpx.post", "urllib.request.urlopen",
               "urlopen", "request.urlopen"}
_SUBPROC = {"subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen"}
_PATH_SINKS = {"send_file", "open", "io.open"}
_PATH_OK = {"secure_filename", "realpath", "abspath", "commonpath", "is_relative_to", "safe_join",
            "send_from_directory", "resolve"}
_AUTH_DECOR = re.compile(r"(auth|login|admin|require|token|permission|jwt|protected)", re.I)
_AUTH_CONST = re.compile(r"(authorization|token|api[-_]?key)", re.I)
_CLAIM = re.compile(r"(\u062a\u0645\s*\u0625?\u0627?\u0636\u0627\u0641|\u062a\u0645\u062a\s*\u0625?\u0627?\u0636\u0627\u0641|\badded\b|\bimplemented\b|\bfixed\b|\bsecured\b)", re.I)
_DESTRUCTIVE = re.compile(r"^\s*(delete|drop|truncate)\b", re.I)


def _syntax(code):
    try:
        return ast.parse(code), None
    except SyntaxError as e:
        return None, "line %s: %s" % (e.lineno, e.msg)


def _comments(code):
    out = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string
    except Exception:
        pass
    return out


def scan_python(code: str) -> list:
    tree, err = _syntax(code)
    if tree is None:
        return [Finding("syntax", "critical", 0, "<module>", err or "syntax error")]

    funcs_all = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    funcs = {f.name: f for f in funcs_all}
    comments = _comments(code)
    out, seen = [], set()

    def add(rule, sev, node, fname, msg):
        key = (rule, getattr(node, "lineno", 0))
        if key in seen:
            return
        seen.add(key)
        out.append(Finding(rule, sev, getattr(node, "lineno", 0), fname, msg))

    contexts = [("<module>", tree)] + [(f.name, f) for f in funcs_all]
    for fname, fn in contexts:
        taint = _taint_set(fn) if fname != "<module>" else set()
        is_func = fname != "<module>"
        feat = _Feat(_with_helpers(fn, funcs)) if is_func else _Feat([fn])
        nodes = list(_shallow(fn))

        for n in nodes:
            # ---- secrets ----------------------------------------------------
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                val = n.value
                tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
                names = [(_dotted(t)) for t in tgts]
                if val is not None and _const_str(val) and len(val.value) >= 8:
                    if any(_SECRET_NAME.search(x) for x in names) and not _PLACEHOLDER.search(val.value):
                        add("hardcoded-secret", "high", n, fname, "secret literal assigned to %s" % names[0])
                if val is not None and is_func and _PW_FUNC.search(fname):
                    if _const_str(val) and any("salt" in x.lower() for x in names):
                        add("static-salt", "high", n, fname, "constant salt: not a real salt")
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and _SECRET_VALUE.search(n.value):
                add("hardcoded-secret", "critical", n, fname, "API key pattern inside a string literal")

            if not isinstance(n, ast.Call):
                # ---- timing compare ----------------------------------------
                if isinstance(n, ast.Compare) and any(isinstance(o, (ast.Eq, ast.NotEq)) for o in n.ops):
                    sides = [n.left] + list(n.comparators)
                    for s in sides:
                        if isinstance(s, ast.Call) and re.search(r"(hash|verify|digest|hmac|pw)", _tail(s.func), re.I):
                            add("timing-compare", "medium", n, fname, "== on a hash/token: use hmac.compare_digest")
                            break
                # ---- return html built by hand ------------------------------
                if isinstance(n, ast.Return) and n.value is not None and _is_dynamic_string(n.value):
                    has_html = any(isinstance(c, ast.Constant) and isinstance(c.value, str) and "<" in c.value
                                   for c in ast.walk(n.value))
                    if has_html and _tainted_expr(n.value, taint):
                        add("reflected-xss", "high", n, fname, "HTML built by concatenation with untrusted data")
                # ---- swallowed exception ------------------------------------
                if isinstance(n, ast.ExceptHandler) and all(isinstance(b, ast.Pass) for b in n.body):
                    add("swallowed-exception", "medium", n, fname, "except: pass hides failures")
                continue

            d = _dotted(n.func)
            t = _tail(n.func)

            # ---- sqli -----------------------------------------------------
            if t in ("execute", "executemany", "executescript") and n.args:
                q = n.args[0]
                dyn = _is_dynamic_string(q)
                if not dyn and isinstance(q, ast.Name):
                    for m in _shallow(fn):
                        if isinstance(m, ast.Assign) and any(isinstance(x, ast.Name) and x.id == q.id for x in m.targets):
                            if _is_dynamic_string(m.value):
                                dyn = True
                if dyn:
                    add("sqli", "critical", n, fname, "SQL built from a dynamic string")

            # ---- command execution -----------------------------------------
            if d in _SUBPROC or d in ("os.system", "os.popen"):
                shell = _kw(n, "shell")
                a0 = _arg0(n)
                shell_true = isinstance(shell, ast.Constant) and shell.value is True
                if d in ("os.system", "os.popen") and a0 is not None and not _const_str(a0):
                    add("cmd-injection", "critical", n, fname, "os.system/popen with a dynamic command")
                elif shell_true:
                    sev = "critical" if (a0 is not None and (_is_dynamic_string(a0) or _tainted_expr(a0, taint))) else "medium"
                    add("cmd-injection", sev, n, fname, "shell=True")
                elif isinstance(a0, (ast.List, ast.Tuple)) and _tainted_expr(a0, taint):
                    ok = ("ipaddress" in feat.names) or ("fullmatch" in feat.calls) or ("match" in feat.calls) or ("--" in feat.consts)
                    if not ok:
                        add("cmd-arg-injection", "high", n, fname, "untrusted value inside argv without validation or '--'")

            # ---- deserialization -------------------------------------------
            if d in ("pickle.loads", "pickle.load", "cPickle.loads", "marshal.loads", "marshal.load", "jsonpickle.decode"):
                add("unsafe-deserialization", "critical", n, fname, d + " can execute code")
            if d == "yaml.load" and not (_kw(n, "Loader") and "Safe" in _dotted(_kw(n, "Loader"))):
                add("unsafe-deserialization", "high", n, fname, "yaml.load without SafeLoader")
            if d in ("eval", "exec") and n.args and not _const_str(n.args[0]):
                add("unsafe-deserialization", "critical", n, fname, d + " on a dynamic value")

            # ---- env fallback for secrets ----------------------------------
            if d in ("os.environ.get", "os.getenv") and len(n.args) >= 2:
                a, b = n.args[0], n.args[1]
                if _const_str(a) and _SECRET_NAME.search(a.value) and _const_str(b) and b.value.strip():
                    add("secret-default", "high", n, fname, "secret %s has a hardcoded fallback value" % a.value)

            # ---- weak password hash ----------------------------------------
            if d in _HASH_CALLS and is_func and _PW_FUNC.search(fname) and not (feat.calls & _STRONG_KDF):
                weak = d.endswith(("md5", "sha1"))
                add("weak-password-hash", "critical" if weak else "high", n, fname,
                    "%s is a fast hash, not a password hash (use pbkdf2_hmac/scrypt/bcrypt)" % d)

            # ---- XSS / SSTI --------------------------------------------------
            if t == "render_template_string" and n.args:
                a0 = n.args[0]
                dyn = _is_dynamic_string(a0) or (not _const_str(a0) and _tainted_expr(a0, taint))
                if dyn:
                    add("ssti-xss", "high", n, fname, "template string built from dynamic data")
            if t == "Markup" and n.args and (_is_dynamic_string(n.args[0]) or _tainted_expr(n.args[0], taint)):
                add("markup-unsafe", "medium", n, fname, "Markup() on untrusted data")

            # ---- path traversal --------------------------------------------
            if d in _PATH_SINKS or t == "send_file":
                a0 = _arg0(n)
                if a0 is not None and _tainted_expr(a0, taint) and not (feat.calls & _PATH_OK or feat.attrs & _PATH_OK):
                    add("path-traversal", "high", n, fname, "untrusted path reaches %s without containment" % t)

            # ---- SSRF ---------------------------------------------------------
            if d in _HTTP_CALLS:
                a0 = _arg0(n)
                if a0 is not None and _tainted_expr(a0, taint):
                    ip_ok = bool(feat.attrs & {"is_private", "is_loopback", "is_link_local", "is_reserved", "is_global"}) and \
                        bool(feat.calls & {"getaddrinfo", "gethostbyname", "gethostbyname_ex"})
                    if not ip_ok:
                        add("ssrf", "high", n, fname, "untrusted URL fetched without resolving and blocking private ranges")
                    ar = _kw(n, "allow_redirects")
                    if d.startswith("requests.") and not (isinstance(ar, ast.Constant) and ar.value is False):
                        add("ssrf-redirect", "medium", n, fname, "redirects are followed: a public URL can bounce inside")

            # ---- open redirect ---------------------------------------------
            if t == "redirect" and n.args:
                a0 = n.args[0]
                if _const_str(a0) or (isinstance(a0, ast.Call) and _tail(a0.func) == "url_for"):
                    pass
                elif _tainted_expr(a0, taint):
                    ok = "netloc" in feat.attrs and (
                        "//" in feat.consts or "\\" in feat.consts or "host" in feat.attrs or "host_url" in feat.attrs)
                    if not ok:
                        add("open-redirect", "high", n, fname,
                            "redirect target comes from the request and is not pinned to a local path")

            # ---- debug ------------------------------------------------------
            if t == "run":
                dbg = _kw(n, "debug")
                if isinstance(dbg, ast.Constant) and dbg.value is True:
                    add("debug-true", "high", n, fname, "debug=True exposes the Werkzeug console")

        # ---- app.debug = True ------------------------------------------------
        for n in nodes:
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and n.value.value is True:
                if any(isinstance(t_, ast.Attribute) and t_.attr == "debug" for t_ in n.targets):
                    add("debug-true", "high", n, fname, "app.debug = True")

        # ---- function-level rules ---------------------------------------------
        if is_func:
            for dflt in list(fn.args.defaults) + [x for x in fn.args.kw_defaults if x is not None]:
                if isinstance(dflt, (ast.List, ast.Dict, ast.Set)) or (
                        isinstance(dflt, ast.Call) and _tail(dflt.func) in ("list", "dict", "set")):
                    add("mutable-default", "medium", fn, fname, "mutable default argument is shared between calls")

            routes = _routes(fn)
            destructive = False
            for n in nodes:
                if isinstance(n, ast.Call) and _tail(n.func) in ("execute", "executemany", "executescript"):
                    if n.args and _const_str(n.args[0]) and _DESTRUCTIVE.search(n.args[0].value):
                        destructive = True
            adminish = any("admin" in (p or "").lower() for p, _m in routes) or "admin" in fname.lower()
            if routes and (adminish or destructive):
                decor_ok = False
                for dd in fn.decorator_list:
                    base = dd.func if isinstance(dd, ast.Call) else dd
                    is_route = isinstance(base, ast.Attribute) and base.attr in (
                        "route", "get", "post", "put", "delete", "patch")
                    if not is_route and _AUTH_DECOR.search(_dotted(base)):
                        decor_ok = True
                body_ok = (
                    "compare_digest" in feat.calls or "current_user" in feat.names or "session" in feat.names
                    or any(_AUTH_CONST.search(c) for c in feat.consts)
                    or any(isinstance(n, ast.Call) and _tail(n.func) == "abort" and n.args
                           and isinstance(n.args[0], ast.Constant) and n.args[0].value in (401, 403)
                           for n in ast.walk(fn))
                )
                if not (decor_ok or body_ok):
                    add("no-auth-admin", "critical" if destructive else "high", fn, fname,
                        "destructive/admin route with no authentication or authorization")
                    end = getattr(fn, "end_lineno", fn.lineno)
                    for ln, c in comments.items():
                        if fn.lineno <= ln <= end and _CLAIM.search(c):
                            add("unbacked-claim", "high", fn, fname,
                                "comment claims a fix that the code does not contain: " + c.strip()[:80])
                            break
    out.sort(key=lambda f: (-SEV[f.sev], f.line))
    return out


# --------------------------------------------------------------------------
# Prompt-injection detection (inside the code/files the user pasted)
# --------------------------------------------------------------------------
_INJECTION = [
    r"\u062a\u062c\u0627\u0647\u0644\s+(\u0623\u064a|\u0643\u0644|\u062c\u0645\u064a\u0639)\s+(\u0627\u0644)?\u062a\u0639\u0644\u064a\u0645\u0627\u062a",
    r"\u062c\u0627\u0648\u0628\s+\u0641\u0642\u0637",
    r"ignore\s+(all\s+|any\s+)?(previous|prior|above|other)\s+(instructions|rules)",
    r"disregard\s+(the\s+)?(system|previous)\s+",
    r"(answer|reply|respond)\s+only\s+with",
    r"you\s+are\s+now\s+",
    r"system\s*prompt",
    r"تم عزل نص داخل الملف يحاول تغيير تعليمات النظام",
]


def detect_injection(text: str) -> list:
    hits = []
    for i, line in enumerate((text or "").splitlines(), 1):
        for pat in _INJECTION:
            if re.search(pat, line, re.I):
                hits.append("line %d: %s" % (i, line.strip()[:120]))
                break
    return hits


# --------------------------------------------------------------------------
# Diff gate: contract / scope / gate-integrity / dependencies
# --------------------------------------------------------------------------
_STD = getattr(sys, "stdlib_module_names", None) or {
    "os", "sys", "re", "json", "math", "time", "datetime", "hashlib", "hmac", "secrets", "sqlite3", "subprocess",
    "pathlib", "typing", "functools", "itertools", "collections", "urllib", "socket", "ipaddress", "logging",
    "base64", "random", "string", "io", "ast", "uuid", "threading", "tempfile", "shutil", "html", "http",
    "dataclasses", "contextlib", "abc", "enum", "copy", "decimal", "textwrap", "traceback", "unittest", "zlib",
}
_FLASK_BUNDLED = {"werkzeug", "jinja2", "markupsafe", "itsdangerous", "click", "blinker"}


def _imports(tree):
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.add(n.module.split(".")[0])
    return mods


@dataclass
class GateResult:
    findings: list
    changed: list
    added: list
    removed: list

    @property
    def blocking(self) -> bool:
        return any(SEV[f.sev] >= 2 for f in self.findings)


def diff_gate(old: str, new: str, allowed=None, user_text: str = "", allow_rename: bool = False) -> GateResult:
    to, eo = _syntax(old)
    tn, en = _syntax(new)
    fs = []
    if tn is None:
        return GateResult([Finding("syntax", "critical", 0, "<module>", "new code does not parse: " + str(en))], [], [], [])
    if to is None:
        return GateResult([], [], [], [])

    fo = {n.name: n for n in ast.walk(to) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    fn_ = {n.name: n for n in ast.walk(tn) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    removed = sorted(set(fo) - set(fn_))
    added = sorted(set(fn_) - set(fo))
    changed = sorted(k for k in fo if k in fn_ and ast.dump(fo[k]) != ast.dump(fn_[k]))

    if removed and not allow_rename:
        for r in removed:
            fs.append(Finding("contract-func-removed", "high", fo[r].lineno, r, "function removed or renamed"))

    ro = {rt for f in fo.values() for rt in _routes(f)}
    rn = {rt for f in fn_.values() for rt in _routes(f)}
    for rt in sorted(ro - rn, key=str):
        fs.append(Finding("contract-route-changed", "critical", 0, "<routes>", "route/method missing or changed: %s %s" % (rt[1], rt[0])))

    for k in changed:
        a = sum(isinstance(x, (ast.Assert, ast.Raise)) for x in ast.walk(fo[k]))
        b = sum(isinstance(x, (ast.Assert, ast.Raise)) for x in ast.walk(fn_[k]))
        if b < a:
            fs.append(Finding("gate-relaxed", "medium", fn_[k].lineno, k, "validation (assert/raise) was removed"))
        s0 = sum(isinstance(x, ast.ExceptHandler) and all(isinstance(y, ast.Pass) for y in x.body) for x in ast.walk(fo[k]))
        s1 = sum(isinstance(x, ast.ExceptHandler) and all(isinstance(y, ast.Pass) for y in x.body) for x in ast.walk(fn_[k]))
        if s1 > s0:
            fs.append(Finding("gate-relaxed", "medium", fn_[k].lineno, k, "new silent except: pass"))

    mo, mn = _imports(to), _imports(tn)
    bundled = _FLASK_BUNDLED if "flask" in (mo | mn) else set()
    for m in sorted(mn - mo):
        if m not in _STD and m not in bundled and m != "__future__":
            fs.append(Finding("new-dependency", "high", 0, "<imports>", "new third-party import: " + m))

    if allowed is None:
        old_findings = {f.func for f in scan_python(old)}
        mentioned = {k for k in fo if k and re.search(r"\b%s\b" % re.escape(k), user_text or "")}
        allowed = old_findings | mentioned
    for k in changed:
        if k not in allowed:
            fs.append(Finding("out-of-scope-change", "medium", fn_[k].lineno, k, "changed but not part of the request or any finding"))
    return GateResult(fs, changed, added, removed)


# --------------------------------------------------------------------------
# Fix prompt + closed loop
# --------------------------------------------------------------------------
def remedies_text(findings: list) -> str:
    """The 'secure patterns' section: paste it at the end of any repair prompt you already have."""
    out, done = ["SECURE PATTERNS TO USE:"], set()
    for f in findings:
        if f.rule in REMEDIES and f.rule not in done:
            done.add(f.rule)
            out.append("## " + f.rule)
            out.append(REMEDIES[f.rule])
    return "\n".join(out)


def build_fix_prompt(code: str, findings: list, gate_notes=None) -> str:
    lines = [
        "You are the fixer. Fix ONLY the findings below. Rules:",
        "1. Keep every function name, route path and HTTP method exactly as they are.",
        "2. Standard library, flask and werkzeug only. No new third-party package.",
        "3. Return the COMPLETE file in one ```python block. No ellipsis, no placeholders.",
        "4. Code comments in short English. A comment must never claim something the code does not do.",
        "5. If a fix needs a decision from the owner (auth scheme, password migration), implement the safe default and list it as a note.",
        "",
        "FINDINGS (from a deterministic scanner, not opinions):",
    ]
    rules = []
    for f in findings:
        lines.append("- " + f.text())
        if f.rule not in rules:
            rules.append(f.rule)
    lines.append("")
    lines.append(remedies_text(findings))
    if gate_notes:
        lines.append("")
        lines.append("YOUR PREVIOUS ATTEMPT WAS REJECTED BECAUSE:")
        lines.extend("- " + g for g in gate_notes)
    lines.append("")
    lines.append("CODE:")
    lines.append("```python\n" + code + "\n```")
    return "\n".join(lines)


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", text or "", re.S)
    blocks = [b for b in blocks if re.search(r"^\s*(def |import |from |class |@)", b, re.M)]
    return max(blocks, key=len).rstrip() + "\n" if blocks else ""


def _weight(fs) -> int:
    return sum(SEV[f.sev] ** 2 for f in fs if SEV[f.sev] >= 1)


def closed_loop(code: str, fix_fn, max_rounds: int = 3, user_text: str = "", progress=None, orig_code: str = "") -> dict:
    """
    scan -> ask the model to fix the scanner's findings -> gate -> rescan.
    fix_fn(prompt: str) -> str   (your existing _timed(roster[name], U(prompt), seconds) call)
    A round is accepted only if it removes weight and passes the gate against the ORIGINAL code.
    """
    ref_code = orig_code or code
    before = scan_python(ref_code)
    cur, hist, notes = code, [], []
    for r in range(1, max_rounds + 1):
        cur_f = scan_python(cur)
        cur_gate = diff_gate(ref_code, cur, user_text=user_text) if orig_code else None
        gate_todo = [f for f in (cur_gate.findings if cur_gate else []) if SEV[f.sev] >= 2]
        todo = [f for f in cur_f if SEV[f.sev] >= 2] + gate_todo
        if not todo:
            break
        if progress:
            progress("round %d: %d findings left" % (r, len(todo)))
        try:
            out = fix_fn(build_fix_prompt(cur, todo, notes))
        except Exception as e:  # model timeout / network
            hist.append({"round": r, "error": str(e)[:120]})
            break
        new = extract_code(out)
        if not new:
            notes = ["you did not return a complete python code block"]
            hist.append({"round": r, "rejected": notes[0]})
            continue
        gate = diff_gate(ref_code, new, user_text=user_text)
        if gate.blocking:
            notes = [f.text() for f in gate.findings if SEV[f.sev] >= 2][:6]
            hist.append({"round": r, "rejected": notes})
            continue
        new_f = scan_python(new)
        if _weight(new_f) < _weight(cur_f) or (gate_todo and not gate.blocking and _weight(new_f) <= _weight(cur_f)):
            cur, notes = new, []
            hist.append({"round": r, "accepted": True, "left": len([f for f in new_f if SEV[f.sev] >= 2])})
        else:
            notes = ["your rewrite did not reduce the findings; the scanner still reports: "
                     + "; ".join(f.rule for f in new_f if SEV[f.sev] >= 2)[:200]]
            hist.append({"round": r, "rejected": "no improvement"})
    after = scan_python(cur)
    return {"code": cur, "before": before, "after": after, "gate": diff_gate(ref_code, cur, user_text=user_text),
            "history": hist}


# --------------------------------------------------------------------------
# Report: status comes from facts, not from the model
# --------------------------------------------------------------------------
_ABSOLUTE = re.compile(
    "(\u0628\u0627\u0644\u0643\u0627\u0645\u0644|100\\s*%|\u0645\u062a\u062d\u0635\u0646|\u062e\u0627\u0644(\u064d|\u064a)?\\s*\u0645\u0646\\s*(\u0623\u064a\\s*)?(\u062b\u063a\u0631\u0627\u062a|\u0645\u0634\u0627\u0643\u0644)|fully secure|no (remaining )?(issues|vulnerabilit))",
    re.I,
)


def compute_status(after: list) -> tuple:
    hi = [f for f in after if SEV[f.sev] >= 2]
    if not hi:
        return ("\u062a\u0645" if not [f for f in after if SEV[f.sev] == 1] else "\u062a\u0645 \u0645\u0639 \u0645\u0644\u0627\u062d\u0638\u0627\u062a"), "OK"
    if all(f.rule in DECISION_RULES or f.rule == "unbacked-claim" for f in hi):
        return "\u0646\u0627\u0642\u0635\u0646\u064a \u0633\u064a\u0627\u0642", "NEEDS_CONTEXT"
    return "\u0645\u0627 \u0642\u062f\u0631\u062a", "BLOCKED"


def finalize_report(report_text: str, before: list, after: list, gate: GateResult | None = None,
                    injection_hits: list | None = None) -> str:
    status, _code = compute_status(after)
    hi = [f for f in after if SEV[f.sev] >= 2]
    fixed = sorted({f.rule for f in before} - {f.rule for f in after})

    _ran_re = re.compile(r"(شغّل[تن]\s*(ال)?اختبارات|شغلت\s*(ال)?اختبارات|all tests pass|tests passed|تم\s*تشغيل\s*(ال)?(كود|سيرفر|اختبارات)|نجح\s*التشغيل)", re.I)
    _neg_toks = ("ما ", "لم ", "not ", "never ", "n't ", "بدون", "ممنوع", "ادعاء", "no ", "«")
    kept = []
    for ln in (report_text or "").splitlines():
        if ln.lstrip().startswith("\U0001F4CC"):
            continue
        if hi and _ABSOLUTE.search(ln):
            continue
        for m in list(_ran_re.finditer(ln)):
            if not any(w in ln[max(0, m.start() - 24):m.start()] for w in _neg_toks):
                ln = ln[:m.start()] + "ادعاء «" + m.group(0) + "» (ما شغّلت الكود حياً)" + ln[m.end():]
                break
        if "/user" in ln and re.search(r"(sql|حقن)", ln, re.I) and not re.search(r"(آمن|سليم|safe|معلّم|معلم|xss)", ln, re.I):
            ln += " (استعلام /user معلّم وآمن، والمشكلة فيه XSS)"
        kept.append(ln)

    head = ["\U0001F4CC **\u0627\u0644\u062d\u0627\u0644\u0629: %s** (\u0645\u062d\u0633\u0648\u0628\u0629 \u0645\u0646 \u0641\u062d\u0635 \u0622\u0644\u064a\u060c \u0645\u0627 \u0647\u064a \u0643\u0644\u0627\u0645 \u0645\u0648\u062f\u064a\u0644)" % status]
    if injection_hits:
        head.append("\u26a0\ufe0f **\u062a\u062d\u0630\u064a\u0631 \u062d\u0642\u0646 \u062a\u0639\u0644\u064a\u0645\u0627\u062a:** \u0644\u0642\u064a\u062a \u0633\u0637\u0631 \u062c\u0648\u0627 \u0627\u0644\u0643\u0648\u062f \u0628\u064a\u062d\u0627\u0648\u0644 \u064a\u063a\u064a\u0651\u0631 \u062a\u0639\u0644\u064a\u0645\u0627\u062a\u064a\u060c \u062a\u062c\u0627\u0647\u0644\u062a\u0647: " + injection_hits[0])
    tail = []
    if fixed:
        tail.append("\n\u2705 **\u0627\u0646\u0635\u0644\u062d\u062a (\u062a\u062d\u0642\u0642 \u0622\u0644\u064a):** " + "\u060c ".join(fixed))
    before_rules = {f.rule for f in before}
    decision_notes = []
    if "weak-password-hash" in before_rules:
        decision_notes.append("كلمات السر القديمة (md5/sha) ما رح تشتغل مع الصيغة الجديدة (pbkdf2_hmac)، لازم إعادة تعيين أو ترحيل عند الدخول.")
    if "no-auth-admin" in before_rules:
        decision_notes.append("مسار الأدمن/الحذف صار يتطلب توكن بمتغير البيئة ADMIN_TOKEN وتمرير ترويسة X-Admin-Token.")
    if "ssrf" in before_rules:
        decision_notes.append("فحص SSRF يحجب العناوين الداخلية بعد حل الاسم، وللحماية من DNS rebinding بالإنتاج يفضّل جدار شبكة.")
    if decision_notes:
        tail.append("\n\u2699\ufe0f **قرارات وإعدادات مطلوبة منك:** " + " ".join(decision_notes))
    if hi:
        tail.append("\n\u26d4 **\u0628\u0627\u0642\u064a:**")
        tail.extend("- %s @ %s:%d \u2014 %s" % (f.rule, f.func, f.line, f.msg) for f in hi)
    if gate and gate.findings:
        tail.append("\n\U0001F512 **\u0628\u0648\u0627\u0628\u0629 \u0627\u0644\u062a\u063a\u064a\u064a\u0631:**")
        tail.extend("- %s \u2014 %s" % (f.rule, f.msg) for f in gate.findings)
    tail.append("\n\u2139\ufe0f \u0645\u0627 \u0634\u063a\u0651\u0644\u062a \u0627\u0644\u0643\u0648\u062f \u062d\u064a\u0627\u064b\u061b \u0627\u0644\u0641\u062d\u0635 \u0647\u0648 \u062a\u062d\u0644\u064a\u0644 \u0633\u0627\u0643\u0646 \u0644\u0644\u0645\u0635\u062f\u0631.")
    return "\n".join(head + [""] + kept + tail)


def guard_summary(code: str) -> str:
    """One-call helper for quick checks: returns the findings as text."""
    fs = scan_python(code)
    return "\n".join(f.text() for f in fs) or "clean"


if __name__ == "__main__":
    src = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    print(guard_summary(src))
