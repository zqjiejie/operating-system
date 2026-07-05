import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from enum import Enum
from tkinter import messagebox


FLOORS = 20
ELEVATORS = 5
MOVE_SECONDS = 0.75
DOOR_SECONDS = 1.25


class Direction(str, Enum):
    UP = "up"
    DOWN = "down"
    IDLE = "idle"


class DoorState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class ElevatorState:
    elevator_id: int
    current_floor: int = 1
    direction: Direction = Direction.IDLE
    door: DoorState = DoorState.CLOSED
    alarm: bool = False
    car_requests: set[int] = field(default_factory=set)
    assigned_hall_requests: set[tuple[int, Direction]] = field(default_factory=set)


class Dispatcher:
    """Thread-safe shared scheduler for all elevator worker threads."""

    def __init__(self, elevator_count: int):
        self.lock = threading.RLock()
        self.states = [ElevatorState(i) for i in range(elevator_count)]
        self.hall_requests: set[tuple[int, Direction]] = set()
        self.hall_assignments: dict[tuple[int, Direction], int] = {}

    def snapshot(self):
        with self.lock:
            return [
                ElevatorState(
                    elevator_id=state.elevator_id,
                    current_floor=state.current_floor,
                    direction=state.direction,
                    door=state.door,
                    alarm=state.alarm,
                    car_requests=set(state.car_requests),
                    assigned_hall_requests=set(state.assigned_hall_requests),
                )
                for state in self.states
            ], set(self.hall_requests)

    def add_hall_request(self, floor: int, direction: Direction):
        with self.lock:
            request = (floor, direction)
            self.hall_requests.add(request)
            self._assign_hall_requests_locked()

    def add_car_request(self, elevator_id: int, floor: int):
        with self.lock:
            state = self.states[elevator_id]
            if not state.alarm and floor != state.current_floor:
                state.car_requests.add(floor)

    def set_alarm(self, elevator_id: int, alarm: bool):
        with self.lock:
            state = self.states[elevator_id]
            state.alarm = alarm
            if alarm:
                state.direction = Direction.IDLE
                for request, assigned_id in list(self.hall_assignments.items()):
                    if assigned_id == elevator_id:
                        del self.hall_assignments[request]
                state.assigned_hall_requests.clear()
                self._assign_hall_requests_locked()

    def request_open(self, elevator_id: int):
        with self.lock:
            state = self.states[elevator_id]
            if state.direction == Direction.IDLE and not state.alarm:
                state.door = DoorState.OPEN

    def request_close(self, elevator_id: int):
        with self.lock:
            state = self.states[elevator_id]
            if not state.alarm:
                state.door = DoorState.CLOSED

    def assign_work(self, elevator_id: int):
        with self.lock:
            state = self.states[elevator_id]
            if state.alarm:
                return

            self._assign_hall_requests_locked()

    def next_target(self, elevator_id: int):
        with self.lock:
            state = self.states[elevator_id]
            if state.alarm:
                return None

            targets = set(state.car_requests)
            targets.update(floor for floor, _direction in state.assigned_hall_requests)
            if not targets:
                state.direction = Direction.IDLE
                return None

            above = sorted(floor for floor in targets if floor > state.current_floor)
            below = sorted((floor for floor in targets if floor < state.current_floor), reverse=True)

            if state.direction == Direction.UP and above:
                return above[0]
            if state.direction == Direction.DOWN and below:
                return below[0]
            if above and below:
                return above[0] if abs(above[0] - state.current_floor) <= abs(below[0] - state.current_floor) else below[0]
            if above:
                return above[0]
            if below:
                return below[0]
            return state.current_floor

    def move_one_floor(self, elevator_id: int, target: int):
        with self.lock:
            state = self.states[elevator_id]
            if state.alarm or target == state.current_floor:
                return False
            state.door = DoorState.CLOSED
            if target > state.current_floor:
                state.current_floor += 1
                state.direction = Direction.UP
            else:
                state.current_floor -= 1
                state.direction = Direction.DOWN
            return True

    def service_current_floor(self, elevator_id: int):
        with self.lock:
            state = self.states[elevator_id]
            if state.alarm:
                return False

            floor = state.current_floor
            should_stop = floor in state.car_requests
            matched_hall = {
                request
                for request in state.assigned_hall_requests
                if request[0] == floor
            }
            if matched_hall:
                should_stop = True

            # If an idle elevator happens to be exactly where a hall call appears,
            # it may answer immediately before that call is formally assigned.
            idle_hall = {
                request
                for request in self.hall_requests
                if request[0] == floor
                and state.direction == Direction.IDLE
                and self.hall_assignments.get(request, elevator_id) == elevator_id
            }
            if idle_hall:
                should_stop = True
                matched_hall.update(idle_hall)

            if not should_stop:
                return False

            state.car_requests.discard(floor)
            for request in matched_hall:
                self.hall_requests.discard(request)
                self.hall_assignments.pop(request, None)
            state.assigned_hall_requests.difference_update(matched_hall)
            state.door = DoorState.OPEN
            state.direction = Direction.IDLE
            return True

    def close_door_after_service(self, elevator_id: int):
        with self.lock:
            state = self.states[elevator_id]
            if not state.alarm:
                state.door = DoorState.CLOSED

    def _assign_hall_requests_locked(self):
        for state in self.states:
            state.assigned_hall_requests = {
                request
                for request in state.assigned_hall_requests
                if request in self.hall_requests
                and self.hall_assignments.get(request) == state.elevator_id
            }

        for request, elevator_id in list(self.hall_assignments.items()):
            if request not in self.hall_requests or self.states[elevator_id].alarm:
                self.hall_assignments.pop(request, None)

        for request in sorted(self.hall_requests):
            if request in self.hall_assignments:
                continue

            floor, direction = request
            candidates = [
                state
                for state in self.states
                if not state.alarm
            ]
            if not candidates:
                continue

            best_state = min(
                candidates,
                key=lambda state: (
                    self._score(state, floor, direction),
                    len(state.car_requests) + len(state.assigned_hall_requests),
                    state.elevator_id,
                ),
            )
            self.hall_assignments[request] = best_state.elevator_id
            best_state.assigned_hall_requests.add(request)

    def _score(self, state: ElevatorState, floor: int, direction: Direction):
        distance = abs(state.current_floor - floor)
        if state.direction == Direction.IDLE:
            return distance

        moving_toward_call = (
            state.direction == Direction.UP
            and floor >= state.current_floor
            and direction == Direction.UP
        ) or (
            state.direction == Direction.DOWN
            and floor <= state.current_floor
            and direction == Direction.DOWN
        )
        return distance - 0.5 if moving_toward_call else distance + FLOORS


class ElevatorWorker(threading.Thread):
    def __init__(self, dispatcher: Dispatcher, elevator_id: int, event_queue: queue.Queue):
        super().__init__(daemon=True)
        self.dispatcher = dispatcher
        self.elevator_id = elevator_id
        self.event_queue = event_queue
        self.running = True

    def run(self):
        while self.running:
            self.dispatcher.assign_work(self.elevator_id)
            if self.dispatcher.service_current_floor(self.elevator_id):
                self.event_queue.put(("update",))
                time.sleep(DOOR_SECONDS)
                self.dispatcher.close_door_after_service(self.elevator_id)
                self.event_queue.put(("update",))
                continue

            target = self.dispatcher.next_target(self.elevator_id)
            if target is None:
                time.sleep(0.15)
                continue

            if self.dispatcher.move_one_floor(self.elevator_id, target):
                self.event_queue.put(("update",))
                time.sleep(MOVE_SECONDS)
            else:
                time.sleep(0.15)


class ElevatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("操作系统课程项目 - 电梯调度系统")
        self.geometry("1280x800")
        self.minsize(1180, 720)

        self.dispatcher = Dispatcher(ELEVATORS)
        self.event_queue: queue.Queue = queue.Queue()
        self.workers = [
            ElevatorWorker(self.dispatcher, elevator_id, self.event_queue)
            for elevator_id in range(ELEVATORS)
        ]

        self.hall_buttons: dict[tuple[int, Direction], list[tk.Button]] = {}
        self.floor_displays: dict[tuple[int, int], tk.Label] = {}
        self.elevator_floor_labels: list[tk.Label] = []
        self.elevator_status_labels: list[tk.Label] = []
        self.car_buttons: list[dict[int, tk.Button]] = []
        self.alarm_buttons: list[tk.Button] = []

        self._build_ui()
        for worker in self.workers:
            worker.start()
        self.after(100, self._process_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.configure(bg="#f6f7f9")
        header = tk.Frame(self, bg="#263238", height=58)
        header.pack(fill="x")
        tk.Label(
            header,
            text="五部互联电梯调度系统",
            fg="white",
            bg="#263238",
            font=("Microsoft YaHei UI", 20, "bold"),
        ).pack(side="left", padx=22, pady=12)
        tk.Label(
            header,
            text="20层 | 线程模拟 | 集中调度",
            fg="#cfd8dc",
            bg="#263238",
            font=("Microsoft YaHei UI", 11),
        ).pack(side="left", pady=18)

        body = tk.PanedWindow(self, orient=tk.HORIZONTAL, sashwidth=6, bg="#e0e0e0")
        body.pack(fill="both", expand=True, padx=12, pady=12)

        hall_panel = tk.Frame(body, bg="white", padx=10, pady=8)
        car_panel = tk.Frame(body, bg="white", padx=10, pady=8)
        body.add(hall_panel, minsize=590)
        body.add(car_panel, minsize=570)

        tk.Label(
            hall_panel,
            text="楼层门口控制区（五部电梯按钮互联）",
            bg="white",
            fg="#263238",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).grid(row=0, column=0, columnspan=12, sticky="w", pady=(0, 8))

        tk.Label(hall_panel, text="楼层", bg="white", width=5, font=("Microsoft YaHei UI", 10, "bold")).grid(row=1, column=0)
        for elevator_id in range(ELEVATORS):
            tk.Label(
                hall_panel,
                text=f"E{elevator_id + 1} 门口",
                bg="white",
                fg="#455a64",
                width=16,
                font=("Consolas", 10, "bold"),
            ).grid(row=1, column=elevator_id + 1, padx=2)

        for floor in range(FLOORS, 0, -1):
            row = FLOORS - floor + 2
            tk.Label(
                hall_panel,
                text=f"{floor:02d}",
                bg="#eceff1",
                fg="#263238",
                width=5,
                relief="ridge",
                font=("Consolas", 10, "bold"),
            ).grid(row=row, column=0, pady=1)
            for elevator_id in range(ELEVATORS):
                doorway = tk.Frame(hall_panel, bg="white")
                doorway.grid(row=row, column=elevator_id + 1, padx=2, pady=1)
                display = tk.Label(
                    doorway,
                    text="01 -",
                    bg="#102027",
                    fg="#80cbc4",
                    width=6,
                    relief="sunken",
                    font=("Consolas", 10, "bold"),
                )
                display.pack(side="left")
                self.floor_displays[(floor, elevator_id)] = display

                up_button = tk.Button(
                    doorway,
                    text="▲",
                    width=2,
                    state=tk.NORMAL if floor < FLOORS else tk.DISABLED,
                    command=lambda selected_floor=floor: self._hall_call(selected_floor, Direction.UP),
                )
                up_button.pack(side="left", padx=(2, 0))
                if floor < FLOORS:
                    self.hall_buttons.setdefault((floor, Direction.UP), []).append(up_button)

                down_button = tk.Button(
                    doorway,
                    text="▼",
                    width=2,
                    state=tk.NORMAL if floor > 1 else tk.DISABLED,
                    command=lambda selected_floor=floor: self._hall_call(selected_floor, Direction.DOWN),
                )
                down_button.pack(side="left", padx=(1, 0))
                if floor > 1:
                    self.hall_buttons.setdefault((floor, Direction.DOWN), []).append(down_button)

        tk.Label(
            car_panel,
            text="电梯轿厢控制区",
            bg="white",
            fg="#263238",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        scroll_area = tk.Frame(car_panel, bg="white")
        scroll_area.pack(fill="both", expand=True)

        canvas = tk.Canvas(scroll_area, bg="white", highlightthickness=0)
        scrollbar = tk.Scrollbar(scroll_area, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        grid = tk.Frame(canvas, bg="white")
        canvas_window = canvas.create_window((0, 0), window=grid, anchor="nw")

        def update_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def stretch_inner_width(event):
            canvas.itemconfigure(canvas_window, width=event.width)

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        grid.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", stretch_inner_width)
        canvas.bind_all("<MouseWheel>", on_mousewheel)

        for elevator_id in range(ELEVATORS):
            frame = tk.LabelFrame(
                grid,
                text=f"电梯 E{elevator_id + 1}",
                bg="white",
                fg="#263238",
                padx=8,
                pady=8,
                font=("Microsoft YaHei UI", 11, "bold"),
            )
            frame.grid(row=elevator_id // 2, column=elevator_id % 2, sticky="nsew", padx=6, pady=6)
            grid.columnconfigure(elevator_id % 2, weight=1)
            grid.rowconfigure(elevator_id // 2, weight=1)
            self._build_elevator_panel(frame, elevator_id)

    def _build_elevator_panel(self, frame: tk.LabelFrame, elevator_id: int):
        status = tk.Frame(frame, bg="white")
        status.pack(fill="x")
        floor_label = tk.Label(
            status,
            text="01",
            bg="#102027",
            fg="#80cbc4",
            width=5,
            font=("Consolas", 22, "bold"),
            relief="sunken",
        )
        floor_label.pack(side="left", padx=(0, 8))
        status_label = tk.Label(
            status,
            text="停止 | 门关",
            bg="white",
            fg="#455a64",
            font=("Microsoft YaHei UI", 11),
        )
        status_label.pack(side="left")
        self.elevator_floor_labels.append(floor_label)
        self.elevator_status_labels.append(status_label)

        controls = tk.Frame(frame, bg="white")
        controls.pack(fill="x", pady=8)
        tk.Button(controls, text="开门", command=lambda: self._open_door(elevator_id), width=7).pack(side="left", padx=2)
        tk.Button(controls, text="关门", command=lambda: self._close_door(elevator_id), width=7).pack(side="left", padx=2)
        alarm_button = tk.Button(
            controls,
            text="报警",
            command=lambda: self._toggle_alarm(elevator_id),
            width=7,
            bg="#ffcdd2",
        )
        alarm_button.pack(side="left", padx=2)
        self.alarm_buttons.append(alarm_button)

        floor_grid = tk.Frame(frame, bg="white")
        floor_grid.pack(fill="x")
        buttons: dict[int, tk.Button] = {}
        for floor in range(FLOORS, 0, -1):
            button = tk.Button(
                floor_grid,
                text=f"{floor:02d}",
                width=3,
                command=lambda selected_floor=floor: self._car_call(elevator_id, selected_floor),
            )
            button.grid(row=(FLOORS - floor) // 4, column=(FLOORS - floor) % 4, padx=2, pady=2, sticky="ew")
            floor_grid.columnconfigure((FLOORS - floor) % 4, weight=1)
            buttons[floor] = button
        self.car_buttons.append(buttons)

    def _hall_call(self, floor: int, direction: Direction):
        self.dispatcher.add_hall_request(floor, direction)
        self._refresh()

    def _car_call(self, elevator_id: int, floor: int):
        self.dispatcher.add_car_request(elevator_id, floor)
        self._refresh()

    def _open_door(self, elevator_id: int):
        self.dispatcher.request_open(elevator_id)
        self._refresh()

    def _close_door(self, elevator_id: int):
        self.dispatcher.request_close(elevator_id)
        self._refresh()

    def _toggle_alarm(self, elevator_id: int):
        states, _hall = self.dispatcher.snapshot()
        alarm = not states[elevator_id].alarm
        self.dispatcher.set_alarm(elevator_id, alarm)
        if alarm:
            messagebox.showwarning("报警", f"电梯 E{elevator_id + 1} 已进入报警暂停状态")
        self._refresh()

    def _process_events(self):
        while True:
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break
        self._refresh()
        self.after(100, self._process_events)

    def _refresh(self):
        states, hall_requests = self.dispatcher.snapshot()
        for state in states:
            direction_text = {
                Direction.UP: "↑",
                Direction.DOWN: "↓",
                Direction.IDLE: "-",
            }[state.direction]
            door_text = "门开" if state.door == DoorState.OPEN else "门关"
            alarm_text = " | 报警" if state.alarm else ""
            self.elevator_floor_labels[state.elevator_id].config(text=f"{state.current_floor:02d}")
            self.elevator_status_labels[state.elevator_id].config(
                text=f"{self._direction_name(state.direction)} | {door_text}{alarm_text}",
                fg="#c62828" if state.alarm else "#455a64",
            )
            self.alarm_buttons[state.elevator_id].config(
                text="解除" if state.alarm else "报警",
                bg="#ef9a9a" if state.alarm else "#ffcdd2",
            )

            for floor in range(1, FLOORS + 1):
                self.floor_displays[(floor, state.elevator_id)].config(
                    text=f"{state.current_floor:02d} {direction_text}",
                    fg="#ffab91" if state.alarm else "#80cbc4",
                )

            for floor, button in self.car_buttons[state.elevator_id].items():
                pending = floor in state.car_requests
                button.config(
                    bg="#fff59d" if pending else "SystemButtonFace",
                    relief="sunken" if pending else "raised",
                )

        for request, buttons in self.hall_buttons.items():
            active = request in hall_requests
            for button in buttons:
                button.config(
                    bg="#fff59d" if active else "SystemButtonFace",
                    relief="sunken" if active else "raised",
                )

    def _direction_name(self, direction: Direction):
        if direction == Direction.UP:
            return "上行"
        if direction == Direction.DOWN:
            return "下行"
        return "停止"

    def _on_close(self):
        for worker in self.workers:
            worker.running = False
        self.destroy()


if __name__ == "__main__":
    app = ElevatorApp()
    app.mainloop()
