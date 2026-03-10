import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import eth_up_down_snr


CONFIG_PATH = os.path.join(".", "last_config.json")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("EthUpDownSnr Launcher")
        self.root.geometry("820x720")

        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.log_queue: queue.Queue[str] = queue.Queue()

        self._build_ui()
        self._load_config()
        self._poll_logs()

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        form = ttk.LabelFrame(main, text="Config", padding=10)
        form.pack(fill="x")

        self.vars: dict[str, tk.StringVar] = {
            "poe_switch_host": tk.StringVar(value="10.1.65.230"),
            "username": tk.StringVar(value="1"),
            "password": tk.StringVar(value="1"),
            "iteration_number": tk.StringVar(value="500"),
            "power_off_duration": tk.StringVar(value="10"),
            "power_on_duration": tk.StringVar(value="70"),
            "iteration_time_limit": tk.StringVar(value="80"),
            "extra_time_limit": tk.StringVar(value="300"),
        }

        row = 0
        self._add_row(form, row, "PoE switch host", "poe_switch_host"); row += 1
        self._add_row(form, row, "Username", "username"); row += 1
        self._add_row(form, row, "Password", "password", show="*"); row += 1
        self._add_row(form, row, "Iterations", "iteration_number"); row += 1
        self._add_row(form, row, "Power off duration (sec)", "power_off_duration"); row += 1
        self._add_row(form, row, "Power on duration (sec)", "power_on_duration"); row += 1
        self._add_row(form, row, "Iteration time limit (sec)", "iteration_time_limit"); row += 1
        self._add_row(form, row, "Extra time limit (sec)", "extra_time_limit"); row += 1

        ports_frame = ttk.LabelFrame(main, text="Ports (1-48)", padding=10)
        ports_frame.pack(fill="x", pady=(10, 0))

        self.port_vars: dict[int, tk.BooleanVar] = {}
        for idx in range(48):
            port_id = idx + 1
            var = tk.BooleanVar(value=True)
            self.port_vars[port_id] = var
            cb = ttk.Checkbutton(ports_frame, text=str(port_id), variable=var)
            cb.grid(row=idx // 12, column=idx % 12, sticky="w", padx=4, pady=2)

        buttons = ttk.Frame(main)
        buttons.pack(fill="x", pady=(10, 0))

        self.start_btn = ttk.Button(buttons, text="Start", command=self.start)
        self.start_btn.pack(side="left")

        self.stop_btn = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        logs_frame = ttk.LabelFrame(main, text="Logs", padding=6)
        logs_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = tk.Text(logs_frame, height=12, wrap="word")
        self.log_text.configure(state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(logs_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _add_row(self, parent: ttk.Frame, row: int, label: str, key: str, show: str | None = None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        entry = ttk.Entry(parent, textvariable=self.vars[key], width=30, show=show or "")
        entry.grid(row=row, column=1, sticky="w", pady=4)

    def _config_from_ui(self) -> dict:
        try:
            selected_ports = [p for p, v in self.port_vars.items() if v.get()]
            return {
                "devices_config": {
                    "poe_switch_host": self.vars["poe_switch_host"].get().strip(),
                    "username": self.vars["username"].get().strip(),
                    "password": self.vars["password"].get(),
                    "ports": selected_ports,
                },
                "test_config": {
                    "iteration_number": int(self.vars["iteration_number"].get()),
                    "power_off_duration": int(self.vars["power_off_duration"].get()),
                    "power_on_duration": int(self.vars["power_on_duration"].get()),
                    "iteration_time_limit": int(self.vars["iteration_time_limit"].get()),
                    "extra_time_limit": int(self.vars["extra_time_limit"].get()),
                },
            }
        except ValueError:
            raise ValueError("Numeric fields must be valid integers.")

    def _load_config(self) -> None:
        if not os.path.exists(CONFIG_PATH):
            return

        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return

        devices = data.get("devices_config", {})
        test = data.get("test_config", {})

        if "poe_switch_host" in devices:
            self.vars["poe_switch_host"].set(str(devices["poe_switch_host"]))
        if "username" in devices:
            self.vars["username"].set(str(devices["username"]))
        if "password" in devices:
            self.vars["password"].set(str(devices["password"]))

        ports = devices.get("ports")
        if isinstance(ports, list):
            for port_id, var in self.port_vars.items():
                var.set(port_id in ports)

        if "iteration_number" in test:
            self.vars["iteration_number"].set(str(test["iteration_number"]))
        if "power_off_duration" in test:
            self.vars["power_off_duration"].set(str(test["power_off_duration"]))
        if "power_on_duration" in test:
            self.vars["power_on_duration"].set(str(test["power_on_duration"]))
        if "iteration_time_limit" in test:
            self.vars["iteration_time_limit"].set(str(test["iteration_time_limit"]))
        if "extra_time_limit" in test:
            self.vars["extra_time_limit"].set(str(test["extra_time_limit"]))

    def _save_config(self, config: dict) -> None:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(config, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def start(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            return

        try:
            config = self._config_from_ui()
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e))
            return

        if not config["devices_config"]["ports"]:
            messagebox.showerror("Invalid input", "Select at least one port.")
            return

        self._save_config(config)

        self.stop_event.clear()
        self._append_log("Starting test...\n")

        self.worker_thread = threading.Thread(
            target=self._run_test_thread,
            args=(config,),
            daemon=True,
        )
        self.worker_thread.start()

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

    def _run_test_thread(self, config: dict) -> None:
        try:
            eth_up_down_snr.main(
                config,
                stop_event=self.stop_event,
                log_callback=self.log_queue.put,
            )
        except Exception as e:
            self.log_queue.put(f"\nError: {e}\n")
        finally:
            self.log_queue.put("\nProcess finished.\n")
            self.root.after(0, self._reset_buttons)

    def _reset_buttons(self) -> None:
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def stop(self) -> None:
        if self.worker_thread is None or not self.worker_thread.is_alive():
            return

        self._append_log("Stopping test...\n")
        self.stop_event.set()

    def _poll_logs(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_logs)

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def on_close(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.stop_event.set()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

