"""
ui.py — واجهة المستخدم الرسومية لعميل ويندوز (CustomTkinter / Tkinter — Section 8).
تتضمن حالة الاتصال، إقران الجهاز، بدء/إيقاف المهمة، زر Push-to-Talk، زر الطوارئ F12، وسجل النشاط.
"""
import threading
import tkinter as tk
from tkinter import messagebox

from .action_executor import StrictActionDispatcher
from .api_client import RynAgentApiClient
from .local_config import load_config, save_config
from .pairing import load_device_token, store_device_token
from .safety import LocalSafetyGate
from .task_loop import DesktopTaskRunner


def launch_desktop_ui() -> None:
    cfg = load_config()
    tok = load_device_token()
    api = RynAgentApiClient(cfg["server_url"], device_token=tok)

    root = tk.Tk()
    root.title("RYN Agent · Windows Execution Client")
    root.geometry("540x620")
    root.configure(bg="#070816")

    def confirm_dialog(cmd):
        return messagebox.askyesno(
            "تأكيد أمان RYN Agent",
            f"هل توافق على تنفيذ العملية التالية؟\n\nالعملية: {cmd.get('action')}\nالسبب: {cmd.get('reason')}\nالخطورة: {cmd.get('risk')}",
        )

    safety = LocalSafetyGate(confirm_callback=confirm_dialog)
    dispatcher = StrictActionDispatcher(dry_run=False)

    log_box = tk.Text(root, height=14, bg="#0d0a26", fg="#eef0ff", font=("Consolas", 10))
    def append_log(msg: str) -> None:
        log_box.insert(tk.END, msg + "\n")
        log_box.see(tk.END)

    runner = DesktopTaskRunner(api, safety, dispatcher, log_cb=append_log)

    # ربط مفتاح الإيقاف الطارئ F12 (Global Emergency Stop Hotkey)
    root.bind("<F12>", lambda _e: (safety.trigger_emergency_stop(), append_log("🛑 [F12] تم تفعيل الإيقاف الطارئ!")))

    header = tk.Label(root, text="🤖 RYN Desktop Agent", bg="#070816", fg="#00ff9d", font=("Segoe UI", 16, "bold"))
    header.pack(pady=8)

    status_lbl = tk.Label(
        root,
        text="🟢 متصل ومقترن" if tok else "⚪ غير مقترن — أدخل كود الإقران السداسي",
        bg="#070816",
        fg="#eef0ff",
        font=("Segoe UI", 10),
    )
    status_lbl.pack(pady=2)

    pair_frame = tk.Frame(root, bg="#070816")
    pair_frame.pack(pady=6)
    code_entry = tk.Entry(pair_frame, width=14, font=("Consolas", 13), justify="center")
    code_entry.pack(side=tk.LEFT, padx=4)

    def do_pair():
        c = code_entry.get().strip()
        try:
            res = api.complete_pairing(c, cfg["device_name"], "windows")
            store_device_token(res["device_id"], res["device_token"])
            status_lbl.config(text=f"🟢 مقترن كـ {res['device_id']}")
            append_log(f"✅ تم إقران الجهاز بنجاح ({res['device_id']})")
        except Exception as e:
            append_log(f"⛔ فشل الإقران: {e}")

    tk.Button(pair_frame, text="🔗 إقران الجهاز", bg="#8b5cf6", fg="#ffffff", command=do_pair).pack(side=tk.LEFT, padx=4)

    goal_entry = tk.Entry(root, width=52, font=("Segoe UI", 11))
    goal_entry.insert(0, "Open Notepad and type Hello from RYN Agent")
    goal_entry.pack(pady=8)

    btn_frame = tk.Frame(root, bg="#070816")
    btn_frame.pack(pady=6)

    def start_task_thread():
        g = goal_entry.get().strip()
        if not g:
            return
        def _work():
            try:
                tr = api.create_task(g)
                append_log(f"🚀 بدأت المهمة {tr['task_id']}: {g}")
                runner.run_task_to_completion(tr["task_id"])
            except Exception as e:
                append_log(f"⛔ خطأ: {e}")
        threading.Thread(target=_work, daemon=True).start()

    tk.Button(btn_frame, text="▶ بدء المهمة", bg="#00ff9d", fg="#031a10", font=("Segoe UI", 10, "bold"), command=start_task_thread).pack(side=tk.LEFT, padx=6)
    tk.Button(
        btn_frame,
        text="🛑 إيقاف طوارئ (F12)",
        bg="#ff5d6c",
        fg="#ffffff",
        font=("Segoe UI", 10, "bold"),
        command=lambda: (safety.trigger_emergency_stop(), append_log("🛑 تم ضغط زر الإيقاف الطارئ!")),
    ).pack(side=tk.LEFT, padx=6)

    log_box.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
    root.mainloop()
