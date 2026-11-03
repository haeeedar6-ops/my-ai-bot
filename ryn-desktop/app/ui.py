"""
ui.py — واجهة المستخدم الرسومية لعميل ويندوز (CustomTkinter / Tkinter — Section 8 & 10).
تدعم التحكم الكتابي والصوتي مع الرد الصوتي المسموع ومفتاح الإيقاف الفوري F12.
"""
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog

from .action_executor import StrictActionDispatcher
from .api_client import RynAgentApiClient
from .local_config import load_config
from .pairing import load_device_token, store_device_token
from .safety import LocalSafetyGate
from .task_loop import DesktopTaskRunner
from .voice import PushToTalkController


def launch_desktop_ui() -> None:
    cfg = load_config()
    tok = load_device_token()
    api = RynAgentApiClient(cfg["server_url"], device_token=tok)
    voice_ctrl = PushToTalkController(api)

    root = tk.Tk()
    root.title("RYN Agent · Windows Execution Client (Voice & Text)")
    root.geometry("560x650")
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

    root.bind("<F12>", lambda _e: (safety.trigger_emergency_stop(), append_log("🛑 [F12] تم تفعيل الإيقاف الطارئ!")))

    header = tk.Label(root, text="🤖 RYN Desktop Agent (صوت وكتابة)", bg="#070816", fg="#00ff9d", font=("Segoe UI", 16, "bold"))
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
            voice_ctrl.speak("تم إقران جهاز الكمبيوتر بنجاح")
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
                append_log(f"🚀 بدأت المهمة الكتابية {tr['task_id']}: {g}")
                voice_ctrl.speak("بدأت تنفيذ المهمة على سطح المكتب")
                res = runner.run_task_to_completion(tr["task_id"])
                if res.get("status") == "COMPLETED":
                    voice_ctrl.speak("اكتملت المهمة بنجاح")
            except Exception as e:
                append_log(f"⛔ خطأ: {e}")
        threading.Thread(target=_work, daemon=True).start()

    def start_voice_task():
        spoken = simpledialog.askstring("🎤 أمر صوتي لـ RYN Agent", "تحدث (أو أمْلِ صوتياً Win+H) بالأمر المطلوب تنفيذه:")
        if not spoken or not spoken.strip():
            return
        goal_entry.delete(0, tk.END)
        goal_entry.insert(0, spoken.strip())
        def _vwork():
            try:
                vr = voice_ctrl.send_voice_goal(spoken.strip(), start_task=True)
                tid = (vr.get("task") or {}).get("task_id") or ""
                append_log(f"🎙️ أمر صوتي: {spoken.strip()} -> {tid}")
                if tid:
                    runner.run_task_to_completion(tid)
            except Exception as e:
                append_log(f"⛔ خطأ بالأمر الصوتي: {e}")
        threading.Thread(target=_vwork, daemon=True).start()

    tk.Button(btn_frame, text="▶ تنفيذ كتابي", bg="#00ff9d", fg="#031a10", font=("Segoe UI", 10, "bold"), command=start_task_thread).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_frame, text="🎤 أمر صوتي (Push-to-Talk)", bg="#a855f7", fg="#ffffff", font=("Segoe UI", 10, "bold"), command=start_voice_task).pack(side=tk.LEFT, padx=5)
    tk.Button(
        btn_frame,
        text="🛑 إيقاف طوارئ (F12)",
        bg="#ff5d6c",
        fg="#ffffff",
        font=("Segoe UI", 10, "bold"),
        command=lambda: (safety.trigger_emergency_stop(), append_log("🛑 تم إيقاف المهمة فوراً!")),
    ).pack(side=tk.LEFT, padx=5)

    log_box.pack(padx=14, pady=10, fill=tk.BOTH, expand=True)
    append_log("💡 اضغط F12 في أي لحظة لإيقاف الوكيل فوراً.")
    root.mainloop()
