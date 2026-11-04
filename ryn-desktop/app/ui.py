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

    def start_task() -> None:
        g = goal_entry.get().strip()
        if not g:
            return

        def _make():
            tr = api.create_task(g)
            append_log(f"🚀 بدأت المهمة {tr['task_id']}: {g}")
            return tr["task_id"]
        run_in_thread(_make)

    def talk() -> None:
        def _make():
            append_log("🎤 احكي هلأ...")
            res = ptt.listen_and_send()
            append_log(f"🎤 سمعتك: {res.get('transcription')}")
            return (res.get("task") or {}).get("task_id")
        run_in_thread(_make)

    tk.Button(btn_frame, text="▶ بدء المهمة", bg="#00ff9d", fg="#031a10", font=("Segoe UI", 10, "bold"), command=start_task).pack(side=tk.LEFT, padx=6)
    tk.Button(btn_frame, text="🎤 تكلم", bg="#3b82f6", fg="#ffffff", font=("Segoe UI", 10, "bold"), command=talk).pack(side=tk.LEFT, padx=6)
    tk.Button(btn_frame, text="🛑 إيقاف طوارئ (F12)", bg="#ff5d6c", fg="#ffffff", font=("Segoe UI", 10, "bold"), command=emergency).pack(side=tk.LEFT, padx=6)

    log_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
    pump()
    root.mainloop()
