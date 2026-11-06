"""
ui.py — واجهة المستخدم الرسومية لعميل ويندوز (Tkinter).
تتضمن حالة الاقتران، إقران الجهاز، بدء/إيقاف المهمة، زر الصوت، زر الطوارئ F12 (عام)، وسجل النشاط.
كل تحديثات الواجهة تتم من الخيط الرئيسي فقط عبر طابور (Tkinter غير آمن بين الخيوط).
"""
import queue
import threading
import tkinter as tk
from tkinter import messagebox

from .action_executor import StrictActionDispatcher
from .api_client import RynAgentApiClient
from .capture import enable_dpi_awareness
from .local_config import load_config
from .pairing import load_device_token, store_device_token
from .safety import LocalSafetyGate
from .task_loop import DesktopTaskRunner
from .voice import PushToTalkController


def launch_desktop_ui() -> None:
    enable_dpi_awareness()
    cfg = load_config()
    tok = load_device_token()
    api = RynAgentApiClient(cfg["server_url"], device_token=tok)

    root = tk.Tk()
    root.title("RYN Agent · Windows Execution Client")
    root.geometry("540x660")
    root.configure(bg="#070816")

    ui_q: "queue.Queue" = queue.Queue()

    def append_log(msg: str) -> None:  # آمنة للاستدعاء من أي خيط
        ui_q.put(("log", msg))

    def confirm_dialog(cmd) -> bool:  # تُستدعى من خيط المهمة
        ev = threading.Event()
        box = {"ok": False}
        ui_q.put(("confirm", cmd, ev, box))
        ev.wait(timeout=60)
        return box["ok"]

    safety = LocalSafetyGate(confirm_callback=confirm_dialog)
    dispatcher = StrictActionDispatcher(dry_run=False)
    runner = DesktopTaskRunner(
        api, safety, dispatcher, log_cb=append_log,
        max_width=int(cfg.get("max_image_width", 1280)), jpeg_quality=int(cfg.get("jpeg_quality", 78)),
    )
    ptt = PushToTalkController(api)
    busy = {"v": False}

    log_box = tk.Text(root, height=14, bg="#0d0a26", fg="#eef0ff", font=("Consolas", 10))

    def pump() -> None:
        try:
            while True:
                item = ui_q.get_nowait()
                if item[0] == "log":
                    log_box.insert(tk.END, item[1] + "\n")
                    log_box.see(tk.END)
                elif item[0] == "status":
                    status_lbl.config(text=item[1])
                elif item[0] == "confirm":
                    _, cmd, ev, box = item
                    box["ok"] = messagebox.askyesno(
                        "تأكيد أمان RYN Agent",
                        f"هل توافق على تنفيذ العملية التالية؟\n\nالعملية: {cmd.get('action')}\nالسبب: {cmd.get('reason')}\nالخطورة: {cmd.get('risk')}",
                    )
                    ev.set()
        except queue.Empty:
            pass
        root.after(100, pump)

    def emergency() -> None:
        safety.trigger_emergency_stop()
        append_log("🛑 تم تفعيل الإيقاف الطارئ!")

    root.bind("<F12>", lambda _e: emergency())
    # F12 عام (يشتغل حتى لو النافذة بالخلفية) — يحتاج pynput، وإن لم يتوفر يبقى F12 داخل النافذة فقط
    try:
        from pynput import keyboard as _kb
        _hk = _kb.GlobalHotKeys({"<f12>": emergency})
        _hk.daemon = True
        _hk.start()
    except Exception:
        pass

    tk.Label(root, text="🤖 RYN Desktop Agent", bg="#070816", fg="#00ff9d", font=("Segoe UI", 16, "bold")).pack(pady=8)

    status_lbl = tk.Label(
        root,
        text="🟢 متصل ومقترن" if tok else "⚪ غير مقترن — أدخل كود الإقران السداسي",
        bg="#070816", fg="#eef0ff", font=("Segoe UI", 10),
    )
    status_lbl.pack(pady=2)

    pair_frame = tk.Frame(root, bg="#070816")
    pair_frame.pack(pady=6)
    code_entry = tk.Entry(pair_frame, width=14, font=("Consolas", 13), justify="center")
    code_entry.pack(side=tk.LEFT, padx=4)

    def do_pair() -> None:
        c = code_entry.get().strip()

        def _work():
            try:
                res = api.complete_pairing(c, cfg["device_name"], "windows")
                store_device_token(res["device_id"], res["device_token"])
                ui_q.put(("status", f"🟢 مقترن كـ {res['device_id']}"))
                append_log(f"✅ تم إقران الجهاز بنجاح ({res['device_id']})")
            except Exception as e:
                append_log(f"⛔ فشل الإقران: {e}")
        threading.Thread(target=_work, daemon=True).start()

    tk.Button(pair_frame, text="🔗 إقران الجهاز", bg="#8b5cf6", fg="#ffffff", command=do_pair).pack(side=tk.LEFT, padx=4)

    goal_entry = tk.Entry(root, width=52, font=("Segoe UI", 11))
    goal_entry.insert(0, "Open Notepad and type Hello from RYN Agent")
    goal_entry.pack(pady=8)

    btn_frame = tk.Frame(root, bg="#070816")
    btn_frame.pack(pady=6)

    def run_in_thread(make_task_id) -> None:
        if busy["v"]:
            append_log("⚠️ في مهمة شغالة حالياً.")
            return
        busy["v"] = True

        def _work():
            try:
                task_id = make_task_id()
                if task_id:
                    res = runner.run_task_to_completion(task_id)
                    append_log(f"🏁 النتيجة: {res.get('status')}")
            except Exception as e:
                append_log(f"⛔ خطأ: {e}")
            finally:
                busy["v"] = False
        threading.Thread(target=_work, daemon=True).start()

    def start_goal(g: str) -> None:
        g = (g or "").strip()
        if not g:
            return

        def _make():
            tr = api.create_task(g)
            append_log(f"🚀 بدأت المهمة {tr['task_id']}: {g}")
            return tr["task_id"]
        run_in_thread(_make)

    def start_task() -> None:
        start_goal(goal_entry.get())

    def talk() -> None:
        def _make():
            append_log("🎤 احكي هلأ...")
            res = ptt.listen_and_send()
            append_log(f"🎤 سمعتك: {res.get('transcription')}")
            return (res.get("task") or {}).get("task_id")
        run_in_thread(_make)

    def open_floating() -> None:
        """يخفي النافذة الرئيسية ويعرض روبوتاً عائماً دائماً فوق كل النوافذ: اضغطه لتكتب أو تحكي أمرك."""
        if not api.device_token:
            append_log("⚠️ أقرن الجهاز أولاً.")
            return
        root.withdraw()
        fw = tk.Toplevel(root)
        fw.overrideredirect(True)
        fw.attributes("-topmost", True)
        fw.configure(bg="#070816")
        sw = fw.winfo_screenwidth()
        fw.geometry(f"+{sw - 120}+140")

        bubble = tk.Label(fw, text="🤖", font=("Segoe UI Emoji", 26), bg="#8b5cf6", fg="#ffffff", width=3, cursor="hand2")
        bubble.pack()
        panel = tk.Frame(fw, bg="#252628", padx=10, pady=10)
        drag = {"x": 0, "y": 0, "moved": False}
        state = {"open": False}

        head = tk.Frame(panel, bg="#252628")
        head.pack(fill=tk.X)
        tk.Label(head, text="🤖 قلي شو أنفّذ؟", bg="#252628", fg="#ffffff", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)

        def close_float() -> None:
            fw.destroy()
            root.deiconify()
        tk.Button(head, text="✕", bg="#252628", fg="#ffffff", bd=0, command=close_float).pack(side=tk.RIGHT)

        entry = tk.Entry(panel, width=34, font=("Segoe UI", 11), bg="#14151a", fg="#ffffff", insertbackground="#ffffff", relief=tk.FLAT)
        entry.pack(pady=6, ipady=5)

        def hide_panel() -> None:
            panel.pack_forget()
            state["open"] = False

        def show_panel() -> None:
            panel.pack(fill=tk.X)
            state["open"] = True
            entry.focus_force()

        def send(_e=None) -> None:
            g = entry.get().strip()
            if not g:
                return
            entry.delete(0, tk.END)
            hide_panel()
            start_goal(g)

        def mic() -> None:
            hide_panel()
            talk()

        entry.bind("<Return>", send)
        row = tk.Frame(panel, bg="#252628")
        row.pack(fill=tk.X)
        tk.Button(row, text="🎤", bg="#3b82f6", fg="#ffffff", bd=0, command=mic).pack(side=tk.LEFT, padx=(0, 6), ipadx=8)
        tk.Button(row, text="▶ نفّذ", bg="#00ff9d", fg="#031a10", bd=0, font=("Segoe UI", 10, "bold"), command=send).pack(side=tk.LEFT, fill=tk.X, expand=True)

        def on_press(e) -> None:
            drag["x"], drag["y"], drag["moved"] = e.x_root, e.y_root, False

        def on_move(e) -> None:
            dx, dy = e.x_root - drag["x"], e.y_root - drag["y"]
            if abs(dx) > 4 or abs(dy) > 4:
                drag["moved"] = True
            fw.geometry(f"+{fw.winfo_x() + dx}+{fw.winfo_y() + dy}")
            drag["x"], drag["y"] = e.x_root, e.y_root

        def on_release(_e) -> None:
            if drag["moved"]:
                return
            if busy["v"]:
                emergency()
            elif state["open"]:
                hide_panel()
            else:
                show_panel()

        bubble.bind("<ButtonPress-1>", on_press)
        bubble.bind("<B1-Motion>", on_move)
        bubble.bind("<ButtonRelease-1>", on_release)

        def refresh() -> None:
            if not fw.winfo_exists():
                return
            if busy["v"]:
                bubble.config(text="⏹", bg="#ff5d6c")
                if state["open"]:
                    hide_panel()
            else:
                bubble.config(text="🤖", bg="#8b5cf6")
            fw.after(400, refresh)
        refresh()

    tk.Button(btn_frame, text="▶ بدء المهمة", bg="#00ff9d", fg="#031a10", font=("Segoe UI", 10, "bold"), command=start_task).pack(side=tk.LEFT, padx=6)
    tk.Button(btn_frame, text="🎤 تكلم", bg="#3b82f6", fg="#ffffff", font=("Segoe UI", 10, "bold"), command=talk).pack(side=tk.LEFT, padx=6)
    tk.Button(btn_frame, text="🫧 روبوت عائم", bg="#8b5cf6", fg="#ffffff", font=("Segoe UI", 10, "bold"), command=open_floating).pack(side=tk.LEFT, padx=6)
    tk.Button(btn_frame, text="🛑 إيقاف طوارئ (F12)", bg="#ff5d6c", fg="#ffffff", font=("Segoe UI", 10, "bold"), command=emergency).pack(side=tk.LEFT, padx=6)

    listen = {"on": True}
    listen_var = tk.BooleanVar(value=True)

    def on_listen_toggle() -> None:
        listen["on"] = bool(listen_var.get())
        append_log("📡 استقبال أوامر الدردشة: " + ("مفعّل" if listen["on"] else "مغلق"))

    tk.Checkbutton(
        root, text="📡 استقبال الأوامر من الدردشة (مثال: افتح واتساب وارسل لأمي مرحبا)", variable=listen_var,
        command=on_listen_toggle, bg="#070816", fg="#eef0ff", selectcolor="#0d0a26", activebackground="#070816",
        activeforeground="#eef0ff",
    ).pack(pady=2)

    def poll_chat_commands() -> None:
        import time as _t
        while True:
            _t.sleep(4)
            try:
                if busy["v"] or not listen["on"] or not api.device_token:
                    continue
                res = api.get_pending()
                if not res.get("pending"):
                    continue
                busy["v"] = True
                append_log(f"📥 وصل أمر من الدردشة: {res.get('goal')}")
                try:
                    out = runner.run_task_to_completion(res["task_id"])
                    append_log(f"🏁 النتيجة: {out.get('status')}")
                finally:
                    busy["v"] = False
            except Exception:
                pass  # شبكة/سيرفر: نعيد المحاولة بالدورة الجاية

    threading.Thread(target=poll_chat_commands, daemon=True).start()

    log_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
    pump()
    root.mainloop()
