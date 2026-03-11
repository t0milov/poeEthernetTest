FORM_DATA = {
    "devices_config": {
        "poe_switch_host": "127.0.0.1",
        "username": "user",
        "password": "password",
        "ports": list(range(1, 25)),
    },
    "test_config": {
        "iteration_number": 500,
        "power_off_duration": 10,
        "power_on_duration": 70,
        "iteration_time_limit": 80,
        "extra_time_limit": 300,
    },
}

import os
import sys
import logging
import socket
import time
import re

from typing import Any, Dict, Optional, Callable
from datetime import timedelta, datetime


MODE = os.environ.get("MODE", "local")
HTTP_URL = os.environ.get("HTTP_URL", "")
LAUNCH_ID = os.environ.get("LAUNCH_ID", "local")
LOG_SOCKET_PATH = os.environ.get("LOG_SOCKET_PATH", "")
CSV_SOCKET_PATH = os.environ.get("CSV_SOCKET_PATH", "")
TIME_SOCKET_PATH = os.environ.get("TIME_SOCKET_PATH", "")

MAC_RE = re.compile(r"([0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}")


class StopRequested(Exception):
    pass


def _open_snr(
    host: str,
    username: str,
    password: str,
    stop_event: Optional["threading.Event"] = None,
    write_log: Optional[Callable[[str], None]] = None,
    enter_config: bool = True,
) -> "TelnetSNRController":
    snr = TelnetSNRController(host, stop_event=stop_event, write_log=write_log)
    snr.write_command(username, b"assword:")
    snr.write_command(password, b"#")
    snr.write_command("enable", b"#")
    snr.write_command("terminal length 0", b"#")
    if enter_config:
        snr.write_command("config", b"(config)#")
    return snr


def _sleep_with_stop(seconds: int, stop_event: Optional["threading.Event"]) -> bool:
    if stop_event is None:
        time.sleep(seconds)
        return False

    end_time = time.time() + seconds
    while time.time() < end_time:
        if stop_event.is_set():
            return True
        time.sleep(0.2)
    return False


def _mac_to_serial(mac: str) -> str:
    parts = re.split(r"[-:]", mac)
    if len(parts) < 3:
        return "NA"
    last_three = parts[-3:]
    try:
        decimals = [str(int(x, 16)) for x in last_three]
    except ValueError:
        return "NA"
    return "".join(decimals)


def _get_port_serials(
    host: str,
    username: str,
    password: str,
    ports: list[int],
    stop_event: Optional["threading.Event"],
    write_log: Optional[Callable[[str], None]],
) -> Dict[int, str]:
    snr = _open_snr(host, username, password, stop_event, write_log, enter_config=True)
    port_to_sn: Dict[int, str] = {}
    try:
        for port_id in ports:
            if stop_event is not None and stop_event.is_set():
                raise StopRequested()

            resp = snr.write_command(f"show mac-address-table interface eth1/0/{port_id}", b"#")
            match = MAC_RE.search(resp)
            if match:
                port_to_sn[port_id] = _mac_to_serial(match.group(0))
            else:
                port_to_sn[port_id] = "NA"
    finally:
        snr.disconnect()

    return port_to_sn


def run_test(
    stop_event: Optional["threading.Event"] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> None:
    tsc = TSystemController(log_callback=log_callback)

    poe_switch_host = FORM_DATA["devices_config"]["poe_switch_host"]
    username = FORM_DATA["devices_config"].get("username", "")
    password = FORM_DATA["devices_config"].get("password", "")
    selected_ports = FORM_DATA["devices_config"].get("ports", list(range(1, 25)))
    all_ports = [p for p in selected_ports if isinstance(p, int) and p > 0]

    iteration_number = FORM_DATA["test_config"]["iteration_number"]
    power_off_duration = FORM_DATA["test_config"]["power_off_duration"]
    power_on_duration = FORM_DATA["test_config"]["power_on_duration"]
    iteration_time_limit = FORM_DATA["test_config"]["iteration_time_limit"]
    extra_time_limit = FORM_DATA["test_config"]["extra_time_limit"]

    if not all_ports:
        tsc.write_log("No ports selected, stopping test")
        return

    extra_monitor_ports: Dict[int, float] = {}
    permanently_excluded_ports: set[int] = set()

    port_up_totals: Dict[int, int] = {p: 0 for p in all_ports}
    tsc.write_csv("#,param|dev," + ",".join(f"dev_{p}" for p in all_ports) + ",")

    try:
        port_serials = _get_port_serials(
            poe_switch_host,
            username,
            password,
            all_ports,
            stop_event,
            tsc.write_log,
        )
        sn_values = ",".join(port_serials.get(p, "NA") for p in all_ports)
        tsc.write_csv(f"sn,serial,{sn_values},")
        tsc.write_log("Port serial numbers: " + ", ".join(f"{p}:{port_serials.get(p, 'NA')}" for p in all_ports))

        for iteration_index in range(iteration_number):
            if stop_event is not None and stop_event.is_set():
                tsc.write_log("Stop requested, ending test")
                return

            tsc.write_log(f"=== Iteration {iteration_index + 1}/{iteration_number} ===")
            tsc.write_log(f"Extra monitor ports: {sorted(extra_monitor_ports.keys())}")
            tsc.write_log(f"Permanently excluded: {sorted(permanently_excluded_ports)}")

            ports_to_cycle = [
                p for p in all_ports
                if p not in extra_monitor_ports and p not in permanently_excluded_ports
            ]

            if ports_to_cycle:
                tsc.write_log(f"Enabling ports: {ports_to_cycle}")
                snr = _open_snr(poe_switch_host, username, password, stop_event, tsc.write_log, enter_config=True)
                for port_id in ports_to_cycle:
                    snr.write_command(f"int eth1/0/{port_id}", b"#")
                    snr.write_command("power inline enable", b"#")
                    snr.write_command("exit", b"(config)#")
                snr.disconnect()

                tsc.write_log(f"Waiting {power_on_duration} sec for devices to boot")
                if _sleep_with_stop(power_on_duration, stop_event):
                    tsc.write_log("Stop requested, ending test")
                    return
            else:
                tsc.write_log("No ports to cycle (all in extra monitor or excluded)")

            check_duration = iteration_time_limit - power_on_duration
            check_start = time.time()

            ports_to_check = set(ports_to_cycle) | set(extra_monitor_ports.keys())
            ports_confirmed_up: set[int] = set()

            tsc.write_log(f"Checking ports for {check_duration} sec: {sorted(ports_to_check)}")

            while time.time() - check_start < check_duration:
                if stop_event is not None and stop_event.is_set():
                    tsc.write_log("Stop requested, ending test")
                    return

                remaining_to_check = ports_to_check - ports_confirmed_up
                if not remaining_to_check:
                    tsc.write_log("All ports are up, stopping check early")
                    break

                snr = _open_snr(poe_switch_host, username, password, stop_event, tsc.write_log, enter_config=True)
                resp = snr.write_command("show interface ethernet status", b"#")
                tsc.write_log(resp)
                snr.disconnect()

                for line in resp.splitlines():
                    match = re.match(r"\s*1/0/(\d+)\s+(UP)/UP", line)
                    if match:
                        port_id = int(match.group(1))
                        if port_id in remaining_to_check and port_id not in ports_confirmed_up:
                            ports_confirmed_up.add(port_id)

                if _sleep_with_stop(2, stop_event):
                    tsc.write_log("Stop requested, ending test")
                    return

            failed_ports = ports_to_check - ports_confirmed_up
            now = time.time()

            for port_id in list(extra_monitor_ports.keys()):
                if port_id in ports_confirmed_up:
                    tsc.write_log(f"Port 1/0/{port_id} recovered, removing from extra monitor")
                    del extra_monitor_ports[port_id]

            for port_id in failed_ports:
                if port_id in ports_to_cycle:
                    tsc.write_log(f"Port 1/0/{port_id} failed, adding to extra monitor")
                    extra_monitor_ports[port_id] = now

            now = time.time()
            for port_id in list(extra_monitor_ports.keys()):
                elapsed = now - extra_monitor_ports[port_id]
                if elapsed > extra_time_limit:
                    tsc.write_log(
                        f"Port 1/0/{port_id} exceeded extra_time_limit "
                        f"({elapsed:.0f}s > {extra_time_limit}s), permanently excluding"
                    )
                    permanently_excluded_ports.add(port_id)
                    del extra_monitor_ports[port_id]

            status_values = ",".join("UP" if p in ports_confirmed_up else "DOWN" for p in all_ports)
            tsc.write_csv(f"{iteration_index + 1},status,{status_values},")

            for p in ports_confirmed_up:
                port_up_totals[p] += 1

            total_values = ",".join(str(port_up_totals[p]) for p in all_ports)
            tsc.write_csv(f",total,{total_values},")

            ports_to_power_off = [p for p in ports_to_cycle if p in ports_confirmed_up]
            if ports_to_power_off:
                tsc.write_log(f"Disabling ports: {ports_to_power_off}")
                snr = _open_snr(poe_switch_host, username, password, stop_event, tsc.write_log, enter_config=True)
                for port_id in ports_to_power_off:
                    snr.write_command(f"int eth1/0/{port_id}", b"#")
                    snr.write_command("no power inline enable", b"#")
                    snr.write_command("exit", b"(config)#")
                snr.disconnect()

            if len(permanently_excluded_ports) == len(all_ports):
                tsc.write_log("All ports permanently excluded, stopping test")
                break

            tsc.write_log(f"Sleeping {power_off_duration} sec before next iteration")
            if _sleep_with_stop(power_off_duration, stop_event):
                tsc.write_log("Stop requested, ending test")
                return
    except StopRequested:
        tsc.write_log("Stop requested, ending test")
        return


class TSystemController:
    def __init__(self, log_callback: Optional[Callable[[str], None]] = None) -> None:
        self.local_launch_mode = MODE != "tsystem"
        self.log_callback = log_callback

        if not self.local_launch_mode:
            self.setup_writer("csv", os.getcwd() + "/out/TSYSTEM_CSV/csvData_" + str(LAUNCH_ID) + ".csv")
            self.setup_writer("log", os.getcwd() + "/out/TSYSTEM_LOGS/logData_" + str(LAUNCH_ID))

            self.log_writer = logging.getLogger("log")
            self.csv_writer = logging.getLogger("csv")

            self.uds_log_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.uds_log_socket.connect(LOG_SOCKET_PATH)

            self.uds_csv_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.uds_csv_socket.connect(CSV_SOCKET_PATH)

            self.uds_time_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.uds_time_socket.connect(TIME_SOCKET_PATH)
        else:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_dir = os.path.join(".", "out", f"data_{timestamp}")
            os.makedirs(out_dir, exist_ok=True)
            self.setup_writer("csv", os.path.join(out_dir, "csv_data.csv"))
            self.setup_writer("log", os.path.join(out_dir, "log_data.txt"))

            self.log_writer = logging.getLogger("log")
            self.csv_writer = logging.getLogger("csv")

    def setup_writer(self, logger_name: str, log_file: str, level: int = logging.INFO) -> None:
        logger = logging.getLogger(logger_name)
        formatter = logging.Formatter("%(message)s")
        file_handler = logging.FileHandler(log_file, mode="w")
        file_handler.setFormatter(formatter)
        logger.setLevel(level)
        logger.addHandler(file_handler)

    def write_csv(self, data: str) -> None:
        self.csv_writer.info(data)

        if not self.local_launch_mode:
            self.uds_csv_socket.sendall(data.encode() + b"\n")

    def write_log(self, data: str) -> None:
        message = f"[ {datetime.now().strftime('%d/%m/%y - %H:%M:%S.%f')[:-3]} ] {data}"
        self.log_writer.info(message)

        if self.log_callback is not None:
            self.log_callback(message + "\n")

        if not self.local_launch_mode:
            self.uds_log_socket.sendall(message.encode() + b"\n")
        else:
            print(message)

    def write_remaining_time(self, value: int) -> None:
        if self.local_launch_mode:
            print(f"[ {datetime.now().strftime('%d/%m/%y - %H:%M:%S.%f')[:-3]} ] Remaining time: {timedelta(seconds=value)}")
        else:
            self.uds_time_socket.sendall(str(value).encode())

    def write_remaiming_time(self, value: int) -> None:
        self.write_remaining_time(value)


class TelnetSNRController:
    def __init__(
        self,
        host: str,
        port: int = 23,
        write_log=lambda data: (print(data), sys.stdout.flush()),
        stop_event: Optional["threading.Event"] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.write_log = write_log
        self.stop_event = stop_event
        self.socket: Optional[socket.socket] = None
        self.timeout = 15.0
        self.connect()

    def connect(self) -> None:
        retry_delay = 3.0
        attempt = 0
        while True:
            if self.stop_event is not None and self.stop_event.is_set():
                raise StopRequested()
            try:
                attempt += 1
                self.disconnect()
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.settimeout(self.timeout)
                self.socket.connect((self.host, self.port))
                time.sleep(1)
                self._read_available()
                return
            except Exception as e:
                if self.stop_event is not None and self.stop_event.is_set():
                    raise StopRequested()
                self.write_log(f"({self.host}:{self.port}) Connection error (attempt {attempt}): {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)

    def _read_until(self, expected: bytes, timeout: Optional[float] = None) -> bytes:
        if self.socket is None:
            raise ValueError(f"({self.host}:{self.port}) Telnet connection error...")

        timeout = timeout if timeout is not None else self.timeout
        self.socket.settimeout(timeout)

        buffer = b""
        while expected not in buffer:
            try:
                chunk = self.socket.recv(1024)
                if not chunk:
                    raise ConnectionError(f"({self.host}:{self.port}) Telnet reading error...")
                buffer += chunk
            except socket.timeout:
                break
        return buffer

    def _read_available(self, timeout: float = 0.5) -> bytes:
        if self.socket is None:
            raise ValueError(f"({self.host}:{self.port}) Telnet connection error...")

        self.socket.settimeout(timeout)
        buffer = b""
        try:
            while True:
                chunk = self.socket.recv(1024)
                if not chunk:
                    break
                buffer += chunk
        except socket.timeout:
            pass
        return buffer

    def disconnect(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None

    def write_command(self, message: str, device_prompt_bytes=b"#", shadow: bool = False) -> str:
        retry_delay = 3.0
        attempt = 0
        while True:
            if self.stop_event is not None and self.stop_event.is_set():
                raise StopRequested()
            try:
                attempt += 1
                if self.socket is None:
                    raise ConnectionError(f"({self.host}:{self.port}) Socket is not connected.")

                # if not shadow:
                #     self.write_log(f"(SNR: {self.host}) {message}")

                self.socket.sendall((message + "\r").encode())
                out = self._read_until(device_prompt_bytes).decode("utf-8", errors="replace")

                if not shadow:
                    self.write_log(f"(SNR: {self.host}) \n\r # {out}")

                return out
            except Exception as e:
                if self.stop_event is not None and self.stop_event.is_set():
                    raise StopRequested()
                self.write_log(f"({self.host}:{self.port}) Send error (attempt {attempt}): {e}. Reconnecting in {retry_delay}s...")
                time.sleep(retry_delay)
                self.connect()


def main(
    config: Optional[Dict[str, Any]] = None,
    stop_event: Optional["threading.Event"] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> None:
    global FORM_DATA
    if config is not None:
        FORM_DATA = config
    run_test(stop_event=stop_event, log_callback=log_callback)


if __name__ == "__main__":
    import json

    config_json = os.environ.get("FORM_DATA_JSON", "")
    if config_json:
        main(json.loads(config_json))
    else:
        main()

