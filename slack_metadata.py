
import json
import pandas as pd
from datetime import datetime
import os
import re
import tkinter as tk
from tkinter import filedialog
import sys

#root = tk.Tk()
#root.withdraw()  # hide main window
#root_folder = filedialog.askdirectory(title="Select Slack export folder")

script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
os.chdir(script_dir)
root_folder = "slack_inputs"

def slack_ts_to_datetime(ts):
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")

all_data = []
global_message_lookup = {}

def classify_conversation(name):
    """Classify conversation type based on folder name."""
    if name.startswith("D"):  # Slack DM IDs look like DXXXXXXXX
        return "direct_message"
    elif "__" in name:  # multi-person DM, folder named like U123__U456
        return "multi_dm"
    else:
        return "channel"  # could be public or private, Slack doesn’t distinguish in the export

# Step 1: Build a lookup of all messages by ts across all convos
for convo_name in os.listdir(root_folder):
    convo_path = os.path.join(root_folder, convo_name)
    if not os.path.isdir(convo_path):
        continue

    for filename in os.listdir(convo_path):
        if filename.endswith(".json"):
            file_path = os.path.join(convo_path, filename)
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    messages = json.load(f)
                except json.JSONDecodeError:
                    print(f"⚠️ Could not parse {filename} in {convo_name}")
                    continue

            for msg in messages:
                if msg.get("type") == "message" and msg.get("ts"):
                    global_message_lookup[msg["ts"]] = msg

# Step 2: Extract messages with conversation info
for convo_name in os.listdir(root_folder):
    convo_path = os.path.join(root_folder, convo_name)
    if not os.path.isdir(convo_path):
        continue

    convo_type = classify_conversation(convo_name)

    for filename in os.listdir(convo_path):
        if filename.endswith(".json"):
            file_path = os.path.join(convo_path, filename)
            with open(file_path, "r", encoding="utf-8") as f:
                try:
                    messages = json.load(f)
                except json.JSONDecodeError:
                    continue

            for msg in messages:
                if msg.get("type") != "message":
                    continue

                user_id = msg.get("user")
                ts = msg.get("ts")
                readable_ts = slack_ts_to_datetime(ts) if ts else None

                thread_ts = msg.get("thread_ts")
                is_reply = thread_ts and thread_ts != ts
                if is_reply != True:
                    is_reply = False

                reactions = [
                    {"emoji": r["name"], "count": r["count"]}
                    for r in msg.get("reactions", [])
                ]

                replies = [reply["user"] for reply in msg.get("replies", [])]

                parent_user = None
                if is_reply and thread_ts in global_message_lookup:
                    parent_user = global_message_lookup[thread_ts].get("user")

                all_data.append({
                    "user": user_id,
                    "timestamp": readable_ts,
                    "reactions": reactions,
                    "replies": replies,
                    "is_reply": is_reply,
                    "parent_user": parent_user,
                    "conversation": convo_name,
                    "conversation_type": convo_type,
                    "source_file": filename
                })

# Create final DataFrame
df = pd.DataFrame(all_data)


output_folder = "slack_outputs"
# os.makedirs(output_folder, exist_ok=True)

output_file = os.path.join(output_folder, "slack_export_clean.csv")
df.to_csv(output_file, index=False, encoding="utf-8")