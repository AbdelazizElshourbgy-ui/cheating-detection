# ===== Cheating Tracking and Alert System (modern Tkinter app) =====
# pip install ultralytics opencv-python pillow customtkinter
import os
import cv2
import sqlite3
import tkinter as tk
from tkinter import filedialog
from datetime import datetime
from PIL import Image
import customtkinter as ctk
from ultralytics import YOLO

MODEL_PATH = "best.pt"
CHEATING_CLASS = "cheating"      # class name in your Roboflow dataset
CONF = 0.4                       # minimum confidence to draw a box
ALERT_CONF = 0.6                 # minimum confidence to raise an alert
ALERT_FRAMES = 8                 # cheating must stay for this many frames in a row
COOLDOWN_SECONDS = 5             # don't repeat the alert for the same student
USE_FIREBASE = False             # True = also save every alert to Firebase Firestore
FIREBASE_KEY = "serviceAccountKey.json"
FIREBASE_BUCKET = "your-project-id.appspot.com"   # Firebase Storage bucket name
# always work relative to this file, so it runs from any folder
os.chdir(os.path.dirname(os.path.abspath(__file__)))
SHOT_DIR = os.path.join("alerts", "screenshots")
os.makedirs(SHOT_DIR, exist_ok=True)

model = YOLO(MODEL_PATH)

# ---------- database (SQLite, one file: alerts.db) ----------
db = sqlite3.connect("alerts.db")
db.execute("""CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT, behavior TEXT, confidence REAL, screenshot TEXT)""")
db.commit()

# ---------- Firebase Firestore (optional) ----------
firestore_db = None
bucket = None
if USE_FIREBASE:
    import firebase_admin
    from firebase_admin import credentials, firestore, storage
    firebase_admin.initialize_app(credentials.Certificate(FIREBASE_KEY),
                                  {"storageBucket": FIREBASE_BUCKET})
    firestore_db = firestore.client()
    bucket = storage.bucket()

# ---------- window ----------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

root = ctk.CTk()
root.title("Exam Guard - Cheating Tracking and Alert System")
root.geometry("1360x760")
if os.path.exists("icon.ico"):   # optional app icon
    root.iconbitmap("icon.ico")
root.grid_columnconfigure(1, weight=1)
root.grid_rowconfigure(0, weight=1)

# left sidebar
side = ctk.CTkFrame(root, width=230, corner_radius=0)
side.grid(row=0, column=0, rowspan=2, sticky="ns")
side.pack_propagate(False)

# center: video + status
center = ctk.CTkFrame(root, fg_color="transparent")
center.grid(row=0, column=1, sticky="nsew", padx=15, pady=15)
center.grid_columnconfigure(0, weight=1)
center.grid_rowconfigure(0, weight=1)
video_label = ctk.CTkLabel(center, text="Upload an exam video to start",
                           font=ctk.CTkFont(size=18), text_color="gray",
                           fg_color="#111111", corner_radius=12)
video_label.grid(row=0, column=0, sticky="nsew")
status = ctk.CTkLabel(center, text="Ready", anchor="w", text_color="gray")
status.grid(row=1, column=0, sticky="ew", pady=(8, 0))

# right: live alerts panel
right = ctk.CTkFrame(root, width=320, corner_radius=12)
right.grid(row=0, column=2, sticky="ns", padx=(0, 15), pady=15)
right.pack_propagate(False)
ctk.CTkLabel(right, text="Live Alerts", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(15, 5))
alerts_panel = ctk.CTkScrollableFrame(right, fg_color="transparent")
alerts_panel.pack(fill="both", expand=True, padx=8, pady=(0, 10))

cap = None
fps = 25
frame_no = 0
last_alert = {}      # student id -> frame number of the last alert
streak = {}          # student id -> number of cheating frames in a row
running = False
cards = []
alert_count = 0


def open_screenshot(path):
    if os.path.exists(path):
        Image.open(path).show()


def add_card(time_text, behavior, conf, path):
    """Add one alert card at the top of the live alerts panel."""
    card = ctk.CTkFrame(alerts_panel, fg_color="#3a1c1c", corner_radius=10)
    if cards:
        card.pack(fill="x", padx=4, pady=5, before=cards[0])
    else:
        card.pack(fill="x", padx=4, pady=5)
    cards.insert(0, card)
    ctk.CTkLabel(card, text="Cheating Alert", text_color="#ff6b6b",
                 font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=12, pady=(8, 0))
    ctk.CTkLabel(card, text=f"{behavior}  |  confidence {conf:.2f}\n{time_text}",
                 justify="left").pack(anchor="w", padx=12)
    ctk.CTkButton(card, text="View screenshot", height=26, fg_color="#5a2a2a",
                  hover_color="#7a3a3a", command=lambda: open_screenshot(path)
                  ).pack(anchor="w", padx=12, pady=8)


def popup(text):
    """Notification window that closes by itself after 3 seconds."""
    win = tk.Toplevel(root)
    win.title("Cheating Alert Detected")
    win.configure(bg="#b3261e")
    win.attributes("-topmost", True)
    win.geometry("+80+80")
    tk.Label(win, text=text, fg="white", bg="#b3261e", font=("Arial", 13, "bold"),
             padx=30, pady=20, justify="left").pack()
    win.after(3000, win.destroy)


def save_alert(frame, behavior, conf):
    global alert_count
    now = datetime.now()
    time_text = now.strftime("%Y-%m-%d %H:%M:%S")
    path = os.path.join(SHOT_DIR, now.strftime("alert_%Y%m%d_%H%M%S_%f") + ".jpg")
    cv2.imwrite(path, frame)
    db.execute("INSERT INTO alerts (time, behavior, confidence, screenshot) VALUES (?,?,?,?)",
               (time_text, behavior, round(conf, 2), path))
    db.commit()
    if firestore_db is not None:
        try:
            url = ""
            if bucket is not None:   # upload the screenshot to Firebase Storage
                blob = bucket.blob("screenshots/" + os.path.basename(path))
                blob.upload_from_filename(path)
                blob.make_public()
                url = blob.public_url
            firestore_db.collection("alerts").add({
                "time": time_text, "behavior": behavior,
                "confidence": round(conf, 2), "screenshot": path,
                "screenshot_url": url})
        except Exception as e:      # a Firebase problem must not stop the video
            print("Firebase error:", e)
    alert_count += 1
    count_label.configure(text=f"Alerts this session: {alert_count}")
    add_card(time_text, behavior, conf, path)
    popup(f"Cheating Alert Detected\nBehavior: {behavior}\n"
          f"Time: {now.strftime('%I:%M %p')}\nScreenshot saved successfully.")


def next_frame():
    global frame_no, running
    if not running:
        return
    ok, frame = cap.read()
    if not ok:
        running = False
        status.configure(text="Video finished")
        return
    frame_no += 1

    result = model.track(frame, persist=True, tracker="bytetrack.yaml",
                         conf=CONF, verbose=False)[0]
    new_alerts = []
    cheating_now = set()
    for box in result.boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        name = model.names[int(box.cls[0])]
        conf = float(box.conf[0])
        student_id = int(box.id[0]) if box.id is not None else -1

        if name == CHEATING_CLASS:
            color = (0, 0, 255)          # red (BGR)
            if conf >= ALERT_CONF:
                cheating_now.add(student_id)
                streak[student_id] = streak.get(student_id, 0) + 1
                last = last_alert.get(student_id, -10**9)
                if streak[student_id] >= ALERT_FRAMES and frame_no - last > fps * COOLDOWN_SECONDS:
                    last_alert[student_id] = frame_no
                    new_alerts.append((name, conf))
        else:
            color = (0, 255, 0)          # green

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"#{student_id} {name} {conf:.2f}" if student_id != -1 else f"{name} {conf:.2f}"
        cv2.putText(frame, label, (x1, max(y1 - 5, 15)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    # reset the streak of students who are not cheating in this frame
    for sid in list(streak):
        if sid not in cheating_now:
            del streak[sid]

    # save after drawing so the screenshot shows the red box
    for name, conf in new_alerts:
        save_alert(frame, name, conf)

    # show the frame
    rgb = cv2.cvtColor(cv2.resize(frame, (880, 495)), cv2.COLOR_BGR2RGB)
    img = ctk.CTkImage(light_image=Image.fromarray(rgb), size=(880, 495))
    video_label.configure(image=img, text="")
    video_label.image = img
    root.after(1, next_frame)


def upload_video():
    global cap, fps, frame_no, running, last_alert, streak
    path = filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv")])
    if not path:
        return
    model.predictor = None       # reset the tracker so IDs start fresh for the new video
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    frame_no = 0
    last_alert = {}
    streak = {}
    running = True
    status.configure(text="Processing: " + os.path.basename(path))
    next_frame()


def stop_video():
    global running
    running = False
    status.configure(text="Stopped")


def show_history():
    win = ctk.CTkToplevel(root)
    win.title("Alert History")
    win.geometry("720x500")
    win.after(200, win.focus)
    ctk.CTkLabel(win, text="Alert History", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(15, 2))
    ctk.CTkLabel(win, text="Click a row to open its screenshot", text_color="gray").pack()
    box = ctk.CTkScrollableFrame(win)
    box.pack(fill="both", expand=True, padx=15, pady=15)
    rows = db.execute("SELECT time, behavior, confidence, screenshot FROM alerts ORDER BY id DESC").fetchall()
    if not rows:
        ctk.CTkLabel(box, text="No alerts yet", text_color="gray").pack(pady=20)
    for t, behavior, conf, path in rows:
        ctk.CTkButton(box, text=f"{t}     {behavior}     {conf:.2f}", anchor="w",
                      fg_color="#2b2b2b", hover_color="#3a3a3a",
                      command=lambda p=path: open_screenshot(p)).pack(fill="x", pady=3)


# ---------- sidebar contents ----------
ctk.CTkLabel(side, text="Exam Guard", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(30, 2))
ctk.CTkLabel(side, text="Cheating Tracking System", text_color="gray").pack(pady=(0, 30))
count_label = ctk.CTkLabel(side, text="Alerts this session: 0", text_color="gray")
count_label.pack(side="bottom", pady=20)
ctk.CTkButton(side, text="Upload Video", height=42, command=upload_video).pack(padx=20, pady=6, fill="x")
ctk.CTkButton(side, text="Stop", height=42, fg_color="#8a2d2d", hover_color="#a33a3a",
              command=stop_video).pack(padx=20, pady=6, fill="x")
ctk.CTkButton(side, text="Alert History", height=42, fg_color="#3b3b3b", hover_color="#4a4a4a",
              command=show_history).pack(padx=20, pady=6, fill="x")

root.mainloop()
