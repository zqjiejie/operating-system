from __future__ import annotations

import base64
import json
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


IMAGE_FILE = "fs_image.json"
DEFAULT_BLOCKS = 128
DEFAULT_BLOCK_SIZE = 64


class FileSystemError(Exception):
    pass


@dataclass
class Node:
    name: str
    kind: str
    parent: Optional["Node"] = None
    first_block: int = -1
    length: int = 0
    children: dict[str, "Node"] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    modified_at: float = field(default_factory=time.time)

    def is_dir(self) -> bool:
        return self.kind == "dir"

    def is_file(self) -> bool:
        return self.kind == "file"

    def to_dict(self) -> dict:
        data = {
            "name": self.name,
            "kind": self.kind,
            "first_block": self.first_block,
            "length": self.length,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }
        if self.is_dir():
            data["children"] = [child.to_dict() for child in self.children.values()]
        return data

    @staticmethod
    def from_dict(data: dict, parent: Optional["Node"] = None) -> "Node":
        node = Node(
            name=data["name"],
            kind=data["kind"],
            parent=parent,
            first_block=data.get("first_block", -1),
            length=data.get("length", 0),
            created_at=data.get("created_at", time.time()),
            modified_at=data.get("modified_at", time.time()),
        )
        if node.is_dir():
            for child_data in data.get("children", []):
                child = Node.from_dict(child_data, node)
                node.children[child.name] = child
        return node


@dataclass
class OpenFile:
    node: Node
    mode: str
    offset: int = 0


class VirtualFileSystem:
    def __init__(self, image_path: str = IMAGE_FILE):
        self.image_path = Path(image_path)
        self.block_count = DEFAULT_BLOCKS
        self.block_size = DEFAULT_BLOCK_SIZE
        self.fat: list[int] = [-1] * self.block_count
        self.blocks: list[bytes] = [b""] * self.block_count
        self.root = Node("/", "dir")
        self.cwd = self.root
        self.open_files: dict[int, OpenFile] = {}
        self.next_fd = 3

    def format(self, block_count: int = DEFAULT_BLOCKS, block_size: int = DEFAULT_BLOCK_SIZE) -> None:
        if block_count <= 0 or block_size <= 0:
            raise FileSystemError("block count and block size must be positive")
        self.block_count = block_count
        self.block_size = block_size
        self.fat = [-1] * block_count
        self.blocks = [b""] * block_count
        self.root = Node("/", "dir")
        self.cwd = self.root
        self.open_files.clear()
        self.next_fd = 3

    def load(self) -> bool:
        if not self.image_path.exists():
            return False
        data = json.loads(self.image_path.read_text(encoding="utf-8"))
        self.block_count = data["block_count"]
        self.block_size = data["block_size"]
        self.fat = data["fat"]
        self.blocks = [base64.b64decode(item) for item in data["blocks"]]
        self.root = Node.from_dict(data["root"])
        self.cwd = self.root
        self.open_files.clear()
        self.next_fd = 3
        return True

    def save(self) -> None:
        data = {
            "block_count": self.block_count,
            "block_size": self.block_size,
            "fat": self.fat,
            "blocks": [base64.b64encode(block).decode("ascii") for block in self.blocks],
            "root": self.root.to_dict(),
        }
        self.image_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def path_of(self, node: Optional[Node] = None) -> str:
        node = node or self.cwd
        if node is self.root:
            return "/"
        parts = []
        while node is not None and node is not self.root:
            parts.append(node.name)
            node = node.parent
        return "/" + "/".join(reversed(parts))

    def resolve(self, path: str, want_parent: bool = False) -> Node | tuple[Node, str]:
        if not path:
            raise FileSystemError("empty path")
        current = self.root if path.startswith("/") else self.cwd
        parts = [part for part in path.split("/") if part and part != "."]
        if want_parent:
            if not parts:
                raise FileSystemError("root has no parent")
            target_name = parts.pop()
        for part in parts:
            if part == "..":
                current = current.parent or self.root
                continue
            if not current.is_dir() or part not in current.children:
                raise FileSystemError(f"path not found: {path}")
            current = current.children[part]
        if want_parent:
            if not current.is_dir():
                raise FileSystemError("parent is not a directory")
            if target_name in ("", ".", "..") or "/" in target_name:
                raise FileSystemError("invalid name")
            return current, target_name
        return current

    def mkdir(self, path: str) -> None:
        parent, name = self.resolve(path, True)
        if name in parent.children:
            raise FileSystemError("entry already exists")
        parent.children[name] = Node(name, "dir", parent=parent)
        parent.modified_at = time.time()

    def rmdir(self, path: str) -> None:
        parent, name = self.resolve(path, True)
        node = parent.children.get(name)
        if node is None or not node.is_dir():
            raise FileSystemError("directory not found")
        if node.children:
            raise FileSystemError("directory is not empty")
        if node is self.cwd:
            raise FileSystemError("cannot remove current directory")
        del parent.children[name]
        parent.modified_at = time.time()

    def create(self, path: str) -> None:
        parent, name = self.resolve(path, True)
        if name in parent.children:
            raise FileSystemError("entry already exists")
        parent.children[name] = Node(name, "file", parent=parent)
        parent.modified_at = time.time()

    def delete_file(self, path: str) -> None:
        parent, name = self.resolve(path, True)
        node = parent.children.get(name)
        if node is None or not node.is_file():
            raise FileSystemError("file not found")
        for opened in self.open_files.values():
            if opened.node is node:
                raise FileSystemError("file is open")
        self._free_chain(node.first_block)
        del parent.children[name]
        parent.modified_at = time.time()

    def rename(self, path: str, new_name: str) -> None:
        parent, name = self.resolve(path, True)
        node = parent.children.get(name)
        if node is None:
            raise FileSystemError("entry not found")
        if new_name in ("", ".", "..") or "/" in new_name:
            raise FileSystemError("invalid name")
        if new_name in parent.children and new_name != name:
            raise FileSystemError("entry already exists")
        del parent.children[name]
        node.name = new_name
        node.modified_at = time.time()
        parent.children[new_name] = node
        parent.modified_at = time.time()

    def open(self, path: str, mode: str = "rw") -> int:
        if mode not in {"r", "w", "rw", "a"}:
            raise FileSystemError("mode must be r, w, rw, or a")
        node = self.resolve(path)
        if not node.is_file():
            raise FileSystemError("not a file")
        if mode == "w":
            self._write_node(node, b"")
        fd = self.next_fd
        self.next_fd += 1
        offset = node.length if mode == "a" else 0
        self.open_files[fd] = OpenFile(node, mode, offset)
        return fd

    def close(self, fd: int) -> None:
        if fd not in self.open_files:
            raise FileSystemError("bad file descriptor")
        del self.open_files[fd]

    def read(self, fd: int, size: Optional[int] = None) -> str:
        opened = self._opened(fd)
        if opened.mode not in {"r", "rw"}:
            raise FileSystemError("file is not opened for reading")
        data = self._read_node(opened.node)
        end = len(data) if size is None else min(len(data), opened.offset + size)
        chunk = data[opened.offset:end]
        opened.offset = end
        return chunk.decode("utf-8", errors="replace")

    def write(self, fd: int, text: str) -> None:
        opened = self._opened(fd)
        if opened.mode not in {"w", "rw", "a"}:
            raise FileSystemError("file is not opened for writing")
        data = bytearray(self._read_node(opened.node))
        payload = text.encode("utf-8")
        if opened.offset > len(data):
            data.extend(b"\x00" * (opened.offset - len(data)))
        data[opened.offset:opened.offset + len(payload)] = payload
        opened.offset += len(payload)
        self._write_node(opened.node, bytes(data))

    def list_dir(self, path: Optional[str] = None) -> list[Node]:
        node = self.resolve(path) if path else self.cwd
        if not node.is_dir():
            raise FileSystemError("not a directory")
        return sorted(node.children.values(), key=lambda item: (item.kind != "dir", item.name.lower()))

    def free_blocks(self) -> int:
        return sum(1 for item in self.fat if item == -1)

    def bitmap(self) -> str:
        return "".join("0" if item == -1 else "1" for item in self.fat)

    def _opened(self, fd: int) -> OpenFile:
        if fd not in self.open_files:
            raise FileSystemError("bad file descriptor")
        return self.open_files[fd]

    def _allocate(self, data: bytes) -> int:
        if not data:
            return -1
        chunks = [data[i:i + self.block_size] for i in range(0, len(data), self.block_size)]
        free = [i for i, item in enumerate(self.fat) if item == -1]
        if len(free) < len(chunks):
            raise FileSystemError("not enough free space")
        used = free[:len(chunks)]
        for idx, block_index in enumerate(used):
            self.blocks[block_index] = chunks[idx]
            self.fat[block_index] = used[idx + 1] if idx + 1 < len(used) else -2
        return used[0]

    def _free_chain(self, first_block: int) -> None:
        current = first_block
        while current >= 0:
            next_block = self.fat[current]
            self.fat[current] = -1
            self.blocks[current] = b""
            current = next_block if next_block >= 0 else -1

    def _read_node(self, node: Node) -> bytes:
        data = bytearray()
        current = node.first_block
        visited = set()
        while current >= 0:
            if current in visited:
                raise FileSystemError("FAT chain loop detected")
            visited.add(current)
            data.extend(self.blocks[current])
            current = self.fat[current] if self.fat[current] >= 0 else -1
        return bytes(data[:node.length])

    def _write_node(self, node: Node, data: bytes) -> None:
        old_first = node.first_block
        new_first = self._allocate(data)
        self._free_chain(old_first)
        node.first_block = new_first
        node.length = len(data)
        node.modified_at = time.time()


def print_help() -> None:
    print(
        "commands:\n"
        "  format [blocks] [block_size]\n"
        "  mkdir <name>             rmdir <name>\n"
        "  ls [-l] [path]           cd <path>        pwd\n"
        "  create <file>            rm <file>\n"
        "  open <file> [r|w|rw|a]   close <fd>\n"
        "  write <fd> <text>        read <fd> [size]\n"
        "  stat <path>              bitmap          help\n"
        "  exit"
    )


def fmt_time(value: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def run_shell() -> None:
    fs = VirtualFileSystem()
    loaded = fs.load()
    print("Simple FS ready. Loaded existing image." if loaded else "Simple FS ready. New empty image.")
    print("Type 'help' for commands.")

    while True:
        try:
            line = input(f"{fs.path_of()}> ").strip()
        except EOFError:
            line = "exit"
        if not line:
            continue
        try:
            args = shlex.split(line)
        except ValueError as exc:
            print(f"error: {exc}")
            continue
        cmd = args[0].lower()
        try:
            if cmd in {"exit", "quit"}:
                fs.save()
                print(f"saved to {fs.image_path}")
                return
            if cmd == "help":
                print_help()
            elif cmd == "format":
                blocks = int(args[1]) if len(args) > 1 else DEFAULT_BLOCKS
                block_size = int(args[2]) if len(args) > 2 else DEFAULT_BLOCK_SIZE
                fs.format(blocks, block_size)
                print(f"formatted: {blocks} blocks, {block_size} bytes/block")
            elif cmd == "mkdir":
                fs.mkdir(args[1])
            elif cmd == "rmdir":
                fs.rmdir(args[1])
            elif cmd == "cd":
                node = fs.resolve(args[1])
                if not node.is_dir():
                    raise FileSystemError("not a directory")
                fs.cwd = node
            elif cmd == "pwd":
                print(fs.path_of())
            elif cmd == "ls":
                long_mode = len(args) > 1 and args[1] == "-l"
                path = args[2] if long_mode and len(args) > 2 else (args[1] if len(args) > 1 and not long_mode else None)
                for node in fs.list_dir(path):
                    if long_mode:
                        first = "-" if node.first_block < 0 else str(node.first_block)
                        print(f"{node.kind:4} {node.length:6} first={first:>3} {fmt_time(node.modified_at)} {node.name}")
                    else:
                        print(node.name + ("/" if node.is_dir() else ""))
            elif cmd == "create":
                fs.create(args[1])
            elif cmd == "rm":
                fs.delete_file(args[1])
            elif cmd == "rename":
                fs.rename(args[1], args[2])
            elif cmd == "open":
                fd = fs.open(args[1], args[2] if len(args) > 2 else "rw")
                print(fd)
            elif cmd == "close":
                fs.close(int(args[1]))
            elif cmd == "write":
                fd = int(args[1])
                text = " ".join(args[2:])
                fs.write(fd, text)
            elif cmd == "read":
                size = int(args[2]) if len(args) > 2 else None
                print(fs.read(int(args[1]), size))
            elif cmd == "stat":
                node = fs.resolve(args[1])
                print(f"path: {fs.path_of(node)}")
                print(f"type: {node.kind}")
                print(f"length: {node.length}")
                print(f"first_block: {node.first_block}")
                print(f"created: {fmt_time(node.created_at)}")
                print(f"modified: {fmt_time(node.modified_at)}")
            elif cmd == "bitmap":
                print(fs.bitmap())
                print(f"free: {fs.free_blocks()}/{fs.block_count}")
            else:
                print("unknown command; type 'help'")
        except (IndexError, ValueError):
            print("error: invalid arguments")
        except FileSystemError as exc:
            print(f"error: {exc}")


if __name__ == "__main__":
    try:
        run_shell()
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(1)
