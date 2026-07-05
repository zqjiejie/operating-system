import argparse
import random
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Sequence, Tuple


INSTRUCTION_COUNT = 320
PAGE_SIZE = 10
PAGE_COUNT = INSTRUCTION_COUNT // PAGE_SIZE
FRAME_COUNT = 4


@dataclass
class AccessRecord:
    step: int
    instruction: int
    page: int
    offset: int
    hit: bool
    frame: int
    physical_address: int
    replaced_page: Optional[int]
    frames: Tuple[Optional[int], ...]


def generate_instruction_sequence(
    total: int = INSTRUCTION_COUNT,
    seed: Optional[int] = None,
) -> List[int]:
    """Generate instruction accesses according to the locality rule in the PPT."""
    rng = random.Random(seed)
    sequence: List[int] = []

    current = rng.randrange(total)
    while len(sequence) < total:
        sequence.append(current)
        if len(sequence) >= total:
            break

        sequence.append((current + 1) % total)
        if len(sequence) >= total:
            break

        if current > 0:
            current = rng.randrange(0, current)
        else:
            current = 0
        sequence.append(current)
        if len(sequence) >= total:
            break

        sequence.append((current + 1) % total)
        if len(sequence) >= total:
            break

        lower = min(current + 2, total - 1)
        if lower < total:
            current = rng.randrange(lower, total)
        else:
            current = rng.randrange(total)

    return sequence


class PageReplacementSimulator:
    def __init__(
        self,
        frame_count: int = FRAME_COUNT,
        page_size: int = PAGE_SIZE,
        algorithm: str = "FIFO",
    ) -> None:
        self.frame_count = frame_count
        self.page_size = page_size
        self.algorithm = algorithm.upper()
        if self.algorithm not in {"FIFO", "LRU"}:
            raise ValueError("algorithm must be FIFO or LRU")

    def run(self, sequence: Sequence[int]) -> Tuple[List[AccessRecord], int, float]:
        frames: List[Optional[int]] = [None] * self.frame_count
        page_to_frame: Dict[int, int] = {}
        fifo_queue: Deque[int] = deque()
        last_used: Dict[int, int] = {}
        records: List[AccessRecord] = []
        page_faults = 0

        for step, instruction in enumerate(sequence, start=1):
            page = instruction // self.page_size
            offset = instruction % self.page_size
            replaced_page: Optional[int] = None

            if page in page_to_frame:
                hit = True
                frame = page_to_frame[page]
            else:
                hit = False
                page_faults += 1

                empty_frame = self._find_empty_frame(frames)
                if empty_frame is not None:
                    frame = empty_frame
                else:
                    if self.algorithm == "FIFO":
                        replaced_page = fifo_queue.popleft()
                    else:
                        replaced_page = min(last_used, key=last_used.get)

                    frame = page_to_frame.pop(replaced_page)
                    last_used.pop(replaced_page, None)

                frames[frame] = page
                page_to_frame[page] = frame
                if self.algorithm == "FIFO":
                    fifo_queue.append(page)

            last_used[page] = step
            physical_address = frame * self.page_size + offset
            records.append(
                AccessRecord(
                    step=step,
                    instruction=instruction,
                    page=page,
                    offset=offset,
                    hit=hit,
                    frame=frame,
                    physical_address=physical_address,
                    replaced_page=replaced_page,
                    frames=tuple(frames),
                )
            )

        fault_rate = page_faults / len(sequence) if sequence else 0.0
        return records, page_faults, fault_rate

    @staticmethod
    def _find_empty_frame(frames: Sequence[Optional[int]]) -> Optional[int]:
        for index, page in enumerate(frames):
            if page is None:
                return index
        return None


def format_frames(frames: Sequence[Optional[int]]) -> str:
    return "[" + ", ".join("--" if page is None else f"{page:02d}" for page in frames) + "]"


def print_report(
    records: Sequence[AccessRecord],
    page_faults: int,
    fault_rate: float,
    algorithm: str,
    verbose: bool,
) -> None:
    print("请求调页存储管理方式模拟")
    print(f"页面大小：{PAGE_SIZE}条指令/页")
    print(f"作业大小：{INSTRUCTION_COUNT}条指令，共{PAGE_COUNT}页")
    print(f"分配内存块：{FRAME_COUNT}块")
    print(f"置换算法：{algorithm.upper()}")
    print()

    shown_records = records if verbose else records[:80]
    print("序号  指令  页号  页内偏移  命中/缺页  内存块  物理地址  置换页  当前内存块")
    print("-" * 82)
    for record in shown_records:
        status = "命中" if record.hit else "缺页"
        replaced = "--" if record.replaced_page is None else f"{record.replaced_page:02d}"
        print(
            f"{record.step:>4}  "
            f"{record.instruction:>4}  "
            f"{record.page:>4}  "
            f"{record.offset:>8}  "
            f"{status:^9}  "
            f"{record.frame:>6}  "
            f"{record.physical_address:>8}  "
            f"{replaced:>6}  "
            f"{format_frames(record.frames)}"
        )

    if not verbose and len(records) > len(shown_records):
        print(f"... 已省略 {len(records) - len(shown_records)} 条记录，可加 --verbose 查看全部。")

    print()
    print(f"总访问指令数：{len(records)}")
    print(f"缺页次数：{page_faults}")
    print(f"缺页率：{fault_rate:.2%}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="请求调页存储管理方式模拟")
    parser.add_argument(
        "-a",
        "--algorithm",
        choices=["FIFO", "LRU", "fifo", "lru"],
        default="FIFO",
        help="页面置换算法，默认 FIFO",
    )
    parser.add_argument(
        "-s",
        "--seed",
        type=int,
        default=None,
        help="随机种子；指定后可复现实验结果",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="显示全部 320 条访问记录",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sequence = generate_instruction_sequence(seed=args.seed)
    simulator = PageReplacementSimulator(algorithm=args.algorithm)
    records, page_faults, fault_rate = simulator.run(sequence)
    print_report(records, page_faults, fault_rate, args.algorithm, args.verbose)


if __name__ == "__main__":
    main()
